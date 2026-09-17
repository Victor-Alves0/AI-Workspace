"""Cliente nativo das APIs públicas e de orquestração do Civitai.

A Site API descobre modelos, versões e imagens. A Orchestration API executa
workflows pagos em Buzz. URLs de resultados são assinadas e expiram; quem chama
deve baixar e persistir os bytes antes de mostrá-los no chat.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import UserSecret
from ..secrets_service import CIVITAI_KEY, get_secret, has_secret, set_secret

SITE_BASE = "https://civitai.com/api/v1"
# O endpoint legado ainda responde, mas não acompanha o contrato de geração
# atual (e passou a rejeitar o antigo ``imageGen.engine=flux``).
ORCHESTRATION_BASE = "https://orchestration-new.civitai.com"
_TIMEOUT = 30.0
_TERMINAL = {"succeeded", "failed", "expired", "canceled"}


def _headers(token: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "AI-Workspace/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _error(service: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 401:
        return {"error": f"{service}: API token inválido ou ausente"}
    if response.status_code == 403:
        return {"error": f"{service}: token sem permissão para esta operação"}
    if response.status_code == 429:
        return {"error": f"{service}: limite de requisições atingido; tente novamente depois"}
    detail = response.text[:500].strip()
    return {"error": f"{service} HTTP {response.status_code}: {detail or 'resposta vazia'}"}


async def get_token(db: AsyncSession, user_id: str) -> str | None:
    return await get_secret(db, uuid.UUID(user_id), CIVITAI_KEY)


async def is_configured(db: AsyncSession, user_id: str) -> bool:
    return await has_secret(db, uuid.UUID(user_id), CIVITAI_KEY)


async def set_token(db: AsyncSession, user_id: str, token: str) -> None:
    await set_secret(db, uuid.UUID(user_id), CIVITAI_KEY, token.strip())


async def delete_token(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        sa_delete(UserSecret).where(
            UserSecret.user_id == uuid.UUID(user_id), UserSecret.name == CIVITAI_KEY
        )
    )
    await db.commit()


def test_connection(token: str) -> dict[str, Any]:
    """Valida o token sem gastar Buzz usando o endpoint autenticado ``/me``."""
    if not token:
        return {"ok": False, "error": "token vazio"}
    try:
        response = httpx.get(f"{SITE_BASE}/me", headers=_headers(token), timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"falha de rede: {exc}"}
    if response.status_code != 200:
        return {"ok": False, **_error("Civitai", response)}
    data = response.json() or {}
    return {
        "ok": True,
        "user": data.get("username") or data.get("name") or str(data.get("id") or "conectado"),
        "tier": data.get("tier"),
    }


def _site_get(token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = httpx.get(
            f"{SITE_BASE}/{path.lstrip('/')}", headers=_headers(token), params=params,
            timeout=_TIMEOUT, follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return {"error": f"Civitai: falha de rede: {exc}"}
    if response.status_code >= 400:
        return _error("Civitai", response)
    data = response.json()
    return data if isinstance(data, dict) else {"items": data}


_CATALOG_RESULT_LIMIT = 4
_CATALOG_VERSION_LIMIT = 2
_CATALOG_TEXT_LIMIT = 160
_GALLERY_RESULT_LIMIT = 4
_DETAIL_DESCRIPTION_LIMIT = 1_200


def _catalog_text(value: Any, limit: int = _CATALOG_TEXT_LIMIT) -> str:
    """Campo público do catálogo: nomes úteis, nunca um prompt/metadata gigante."""
    return " ".join(str(value or "").split())[:limit]


def _compact_version(
    version: dict[str, Any], *, include_triggers: bool = False
) -> dict[str, Any]:
    out = {
        "id": version.get("id"),
        "name": _catalog_text(version.get("name")),
        "base_model": _catalog_text(version.get("baseModel")),
        "air": version.get("air"),
        "can_generate": version.get("canGenerate", version.get("supportsGeneration")),
    }
    # Gatilhos podem ajudar ao consultar UMA versão específica, mas devolvê-los em
    # toda busca de catálogo multiplica o contexto por dezenas de milhares de tokens.
    if include_triggers:
        out["trained_words"] = [
            _catalog_text(word, 120) for word in (version.get("trainedWords") or [])[:6]
        ]
    return out


def _compact_model(model: dict[str, Any], *, include_description: bool = False) -> dict[str, Any]:
    creator = model.get("creator") or {}
    out: dict[str, Any] = {
        "id": model.get("id"),
        "name": _catalog_text(model.get("name")),
        "type": _catalog_text(model.get("type")),
        "creator": _catalog_text(creator.get("username")),
        "nsfw": model.get("nsfw"),
        "versions": [
            _compact_version(v, include_triggers=include_description)
            for v in (model.get("modelVersions") or [])[:_CATALOG_VERSION_LIMIT]
        ],
    }
    if include_description:
        description = str(model.get("description") or "")
        out["description"] = description[:_DETAIL_DESCRIPTION_LIMIT]
    return out


def search_models(
    token: str = "", query: str = "", model_type: str = "", base_model: str = "",
    sort: str = "Highest Rated", period: str = "AllTime", limit: int = 10,
    page: int = 1, nsfw: bool = False, supports_generation: bool = False,
    cursor: str = "",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        # Esta é uma ferramenta para o agente escolher um modelo, não uma página de
        # catálogo. Quatro opções compactas dão decisão suficiente e evitam reencaminhar
        # uma avalanche de versões/gatilhos a cada iteração do loop.
        "limit": max(1, min(int(limit), _CATALOG_RESULT_LIMIT)),
        "sort": sort or "Highest Rated",
        "period": period or "AllTime",
        "nsfw": "true" if nsfw else "false",
    }
    if query.strip():
        params["query"] = query.strip()
        # Full-text search uses opaque cursors; Civitai rejects query + page.
        if cursor.strip():
            params["cursor"] = cursor.strip()
    else:
        params["page"] = max(1, int(page))
    if model_type.strip():
        params["types"] = model_type.strip()
    if base_model.strip():
        params["baseModels"] = base_model.strip()
    if supports_generation:
        params["supportsGeneration"] = "true"
    data = _site_get(token, "models", params)
    if data.get("error"):
        return data
    return {
        "items": [
            _compact_model(item)
            for item in (data.get("items") or [])[:_CATALOG_RESULT_LIMIT]
        ],
        "metadata": {"next_cursor": (data.get("metadata") or {}).get("nextCursor")},
    }


def get_model(token: str, model_id: int) -> dict[str, Any]:
    data = _site_get(token, f"models/{int(model_id)}")
    return data if data.get("error") else _compact_model(data, include_description=True)


def get_model_version(token: str, version_id: int) -> dict[str, Any]:
    data = _site_get(token, f"model-versions/{int(version_id)}")
    if data.get("error"):
        return data
    out = _compact_version(data, include_triggers=True)
    # `version` existe para obter o AIR e os gatilhos de uma versão escolhida.
    # Arquivos, downloads e previews longos não entram no workflow de geração e
    # faziam uma consulta simples carregar milhares de caracteres no próximo turno.
    out["description"] = _catalog_text(data.get("description"), _DETAIL_DESCRIPTION_LIMIT)
    return out


_MODEL_URL_RE = re.compile(r"(?:^|/)models/(\d+)(?:[/?#]|$)", re.IGNORECASE)
_VERSION_URL_RE = re.compile(r"(?:modelVersionId=|/model-versions/)(\d+)", re.IGNORECASE)


def _model_reference(token: str, model: str, model_id: int | None, version_id: int | None) -> dict[str, Any]:
    """Converte URL/ID/AIR da pessoa para o contrato de geração atual.

    A normalização acontece dentro da tool, evitando um loop do agente de buscar
    catálogo, descobrir um AIR, testar engines e repetir a geração.
    """
    raw_model = (model or "").strip()
    parsed_model_id = int(model_id or 0)
    parsed_version_id = int(version_id or 0)
    if raw_model.startswith("urn:air:"):
        parts = raw_model.split(":")
        resource_type = parts[2].lower() if len(parts) > 2 else ""
        if resource_type in {"lora", "lycoris", "locon", "dora"}:
            return {"additionalNetworks": {raw_model: {"type": "Lora", "strength": 1.0}}}
        return {"model": raw_model}
    if raw_model:
        version_match = _VERSION_URL_RE.search(raw_model)
        model_match = _MODEL_URL_RE.search(raw_model)
        if version_match:
            parsed_version_id = int(version_match.group(1))
        if model_match:
            parsed_model_id = int(model_match.group(1))
        elif raw_model.isdigit() and not parsed_model_id:
            parsed_model_id = int(raw_model)
        elif not (version_match or raw_model.isdigit()):
            return {"error": "modelo inválido: use uma URL do Civitai, ID ou AIR URN"}

    if not (parsed_model_id or parsed_version_id):
        return {}
    if not parsed_version_id:
        catalog = _site_get(token, f"models/{parsed_model_id}")
        if catalog.get("error"):
            return catalog
        versions = catalog.get("modelVersions") or []
        selected = next((v for v in versions if v.get("canGenerate")), None) or (versions[0] if versions else None)
        if not selected or not selected.get("id"):
            return {"error": "o modelo selecionado não possui versão utilizável para geração"}
        parsed_version_id = int(selected["id"])
    version = _site_get(token, f"model-versions/{parsed_version_id}")
    if version.get("error"):
        return version
    if parsed_model_id and int(version.get("modelId") or 0) not in {0, parsed_model_id}:
        return {"error": "a versão indicada não pertence ao modelo selecionado"}
    air = str(version.get("air") or "").strip()
    if not air:
        return {"error": "a versão selecionada não possui AIR URN para geração"}
    resource_type = str((version.get("model") or {}).get("type") or "").lower()
    if resource_type in {"lora", "lycoris", "locon", "dora"}:
        return {"additionalNetworks": {air: {"type": "Lora", "strength": 1.0}}}
    return {"model": air}


def search_images(
    token: str = "", model_id: int | None = None, version_id: int | None = None,
    username: str = "", sort: str = "Most Reactions", period: str = "AllTime",
    limit: int = 10, page: int = 1, nsfw: str = "None",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        # A galeria é referência visual para o agente, não um feed a ser copiado
        # para o contexto. Quatro cartões com metadados mínimos bastam para escolher
        # uma referência sem reencaminhar URLs, prompts e estatísticas enormes.
        "limit": max(1, min(int(limit), _GALLERY_RESULT_LIMIT)), "page": max(1, int(page)),
        "sort": sort or "Most Reactions", "period": period or "AllTime",
        "nsfw": nsfw or "None",
    }
    if model_id:
        params["modelId"] = int(model_id)
    if version_id:
        params["modelVersionId"] = int(version_id)
    if username.strip():
        params["username"] = username.strip()
    data = _site_get(token, "images", params)
    if data.get("error"):
        return data
    return {
        "items": [
            {
                "id": item.get("id"),
                "width": item.get("width"), "height": item.get("height"),
                "nsfw_level": item.get("nsfwLevel"),
                "username": _catalog_text(item.get("username")),
                "generation": {
                    key: _catalog_text((item.get("meta") or {}).get(key), 240)
                    for key in ("Model", "Sampler", "steps", "cfgScale", "seed")
                    if (item.get("meta") or {}).get(key) is not None
                },
            }
            for item in (data.get("items") or [])[:_GALLERY_RESULT_LIMIT]
        ],
        "metadata": {"next_cursor": (data.get("metadata") or {}).get("nextCursor")},
    }


def _workflow_request(
    token: str, method: str, path: str, *, body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not token:
        return {"error": "Civitai não conectado; configure o API token primeiro"}
    try:
        response = httpx.request(
            method, f"{ORCHESTRATION_BASE}/{path.lstrip('/')}",
            headers={**_headers(token), "Content-Type": "application/json"},
            json=body, params=params, timeout=105.0,
        )
    except httpx.HTTPError as exc:
        return {"error": f"Civitai Orchestration: falha de rede: {exc}"}
    if response.status_code >= 400:
        return _error("Civitai Orchestration", response)
    data = response.json()
    return data if isinstance(data, dict) else {"error": "resposta inesperada do Civitai"}


def submit_image(
    token: str, prompt: str, *, engine: str = "", model: str = "",
    model_id: int | None = None, version_id: int | None = None, ecosystem: str = "",
    width: int = 1024, height: int = 1024, quantity: int = 1,
    negative_prompt: str = "", seed: int | None = None, options_json: str = "",
    mature: bool = False, whatif: bool = False, wait_seconds: int = 60,
) -> dict[str, Any]:
    """Submete uma geração atual em uma única chamada.

    O payload antigo ``imageGen.engine=flux`` passou a ser rejeitado pelo
    orquestrador e induzia novas tentativas caras. O contrato atual usa
    ``textToImage``; URL/ID de modelo são resolvidos internamente.
    """
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return {"error": "prompt é obrigatório"}
    step_input: dict[str, Any] = {
        "prompt": clean_prompt[:4000],
        "width": max(64, min(int(width), 4096)),
        "height": max(64, min(int(height), 4096)),
        "quantity": max(1, min(int(quantity), 4)),
    }
    selected = _model_reference(token, model, model_id, version_id)
    if selected.get("error"):
        return selected
    step_input.update(selected)
    if negative_prompt.strip():
        step_input["negativePrompt"] = negative_prompt.strip()[:4000]
    if seed is not None:
        step_input["seed"] = int(seed)
    if options_json.strip():
        if len(options_json) > 12_000:
            return {"error": "options_json grande demais"}
        try:
            advanced = json.loads(options_json)
        except json.JSONDecodeError as exc:
            return {"error": f"options_json inválido: {exc.msg}"}
        if not isinstance(advanced, dict):
            return {"error": "options_json deve ser um objeto JSON"}
        # Identidade do passo e callbacks pertencem ao envelope, nunca ao input.
        for key in ("$type", "callbacks", "tags", "metadata", "allowMatureContent", "engine", "ecosystem"):
            advanced.pop(key, None)
        step_input.update(advanced)
    body = {
        "steps": [{"$type": "textToImage", "name": "image_0", "input": step_input}],
        # Sem isto o orquestrador esconde URLs de mídia madura e o chat não
        # consegue baixar o resultado. Permissão/saldo continuam validados pela API.
        "allowMatureContent": bool(mature),
    }
    return _workflow_request(
        token, "POST", "v2/consumer/workflows", body=body,
        params={
            "whatif": "true" if whatif else "false",
            "wait": max(0, min(int(wait_seconds), 90)),
            "hideMatureContent": "false" if mature else "true",
        },
    )


def get_workflow(token: str, workflow_id: str) -> dict[str, Any]:
    workflow_id = workflow_id.strip()
    if not workflow_id or "/" in workflow_id or ".." in workflow_id:
        return {"error": "workflow_id inválido"}
    return _workflow_request(token, "GET", f"v2/consumer/workflows/{workflow_id}")


def wait_workflow(
    token: str, workflow_id: str, budget_s: float = 60.0,
    poll_s: float = 3.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, budget_s)
    last: dict[str, Any] = {"id": workflow_id, "status": "pending"}
    while time.monotonic() < deadline:
        last = get_workflow(token, workflow_id)
        if last.get("error") or str(last.get("status") or "").lower() in _TERMINAL:
            return last
        time.sleep(poll_s)
    return last


def workflow_outputs(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrai URLs de mídia das variantes ``images``/``videos``/``blobs``."""
    outputs: list[dict[str, Any]] = []
    for step in workflow.get("steps") or []:
        output = step.get("output") or {}
        for key in ("images", "videos", "audio", "blobs"):
            values = output.get(key) or []
            if isinstance(values, dict):
                values = [values]
            for item in values if isinstance(values, list) else []:
                if isinstance(item, str):
                    url, mime = item, ""
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("downloadUrl") or ""
                    mime = item.get("mimeType") or item.get("contentType") or ""
                else:
                    continue
                if url:
                    kind = "video" if key == "videos" or str(mime).startswith("video/") else "image"
                    outputs.append({"url": url, "mime": mime, "kind": kind})
    return outputs


def download(url: str, max_bytes: int = 100 * 1024 * 1024) -> tuple[bytes, str] | dict[str, Any]:
    """Baixa uma URL assinada devolvida pelo orquestrador antes que expire."""
    if not url.startswith("https://"):
        return {"error": "URL de resultado inválida"}
    try:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
            if response.status_code >= 400:
                return {"error": f"download do Civitai falhou (HTTP {response.status_code})"}
            mime = response.headers.get("content-type", "application/octet-stream").split(";")[0]
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    return {"error": "resultado do Civitai grande demais (>100MB)"}
                chunks.append(chunk)
            return b"".join(chunks), mime
    except httpx.HTTPError as exc:
        return {"error": f"download do Civitai falhou: {exc}"}
