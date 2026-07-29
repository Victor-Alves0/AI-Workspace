"""Provedores customizados compatíveis com OpenAI — config POR-USUÁRIO.

O usuário adiciona endpoints OpenAI-compatíveis (kie.ai, LiteLLM, ou qualquer proxy
que exponha `/v1/chat/completions` + `/v1/models`), cada um com nome, base URL e chave
de API (cifrada). Os modelos de cada provedor entram em todos os seletores prefixados
com ``@<slug>/<model_id>`` e roteiam para a base do provedor com a chave dele.

Espelha a integração Ollama ([[ollama-integration]]): mesma ideia, mas com CHAVE (o
segredo fica cifrado via secrets_service) e VÁRIOS provedores por usuário. O prefixo
``@`` marca um id de modelo de provedor customizado — o OpenRouter nunca começa com
``@``, então não há colisão com `vendor/model` (ex.: `openai/gpt-4o`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..app_config import get_setting, set_setting
from ..secrets_service import delete_secret, get_secret, has_secret, set_secret

logger = logging.getLogger(__name__)

# Marca de id de modelo de provedor customizado: ``@<slug>/<model_id>``.
MODEL_PREFIX = "@"

# Presets conhecidos: pré-preenchem a UI (o usuário edita/testa). Cada base é a raiz
# OpenAI-compatível — batemos em ``{base}/chat/completions``. A base pode conter o
# marcador ``{model}``, substituído pelo id do modelo na hora de usar (para provedores
# que colocam o modelo no CAMINHO em vez do corpo e não expõem ``/models``).
#   - LiteLLM: proxy OpenAI-padrão em `/v1` com `/models` funcionando.
PRESETS: dict[str, dict[str, Any]] = {
    "litellm": {
        "name": "LiteLLM",
        "base_url": "http://localhost:4000/v1",
        "models": [],
    },
}


def _cfg_key(user_id: str) -> str:
    return f"providers:{user_id}"


def _secret_name(slug: str) -> str:
    return f"provider_key:{slug}"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "provedor"


def _norm_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def _container_host(u: str) -> str:
    """Dentro do container, o "localhost" digitado pelo usuário é a MÁQUINA dele, não
    o container — reescreve p/ host.docker.internal (Docker Desktop). Ex.: LiteLLM
    rodando em http://localhost:4000. Aplicado só na hora de USAR."""
    import os
    if os.path.exists("/.dockerenv"):
        return re.sub(r"//(localhost|127\.0\.0\.1)(?=[:/]|$)", "//host.docker.internal", u, count=1)
    return u


async def _items(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    raw = await get_setting(db, _cfg_key(user_id))
    items = raw.get("items") if isinstance(raw, dict) else None
    return [dict(it) for it in items] if isinstance(items, list) else []


async def _save_items(db: AsyncSession, user_id: str, items: list[dict[str, Any]]) -> None:
    await set_setting(db, _cfg_key(user_id), {"items": items})


def slug_from_model(model: str) -> str | None:
    """``@<slug>/<model_id>`` -> ``<slug>`` (ou None se não for id de provedor)."""
    if not model or not model.startswith(MODEL_PREFIX):
        return None
    return model[len(MODEL_PREFIX):].split("/", 1)[0] or None


def _real_model(model: str) -> str:
    """``@<slug>/<model_id>`` -> ``<model_id>`` (o id como o provedor o conhece)."""
    rest = model[len(MODEL_PREFIX):] if model.startswith(MODEL_PREFIX) else model
    parts = rest.split("/", 1)
    return parts[1] if len(parts) == 2 else rest


def _parse_models(value: Any) -> list[str]:
    """Aceita lista OU texto (vírgula/quebra de linha) → lista de ids limpos."""
    if isinstance(value, str):
        parts = re.split(r"[\n,]+", value)
    elif isinstance(value, list):
        parts = [str(v) for v in value]
    else:
        return []
    return [p.strip() for p in parts if p.strip()]


def _apply_model(base: str, model: str) -> str:
    """Substitui o marcador ``{model}`` na base pelo id real do modelo. Sem marcador,
    devolve a base como está (provedor OpenAI-padrão que usa o modelo no corpo)."""
    return base.replace("{model}", model) if "{model}" in base else base


async def list_configs(db: AsyncSession, user_id) -> list[dict[str, Any]]:
    """Provedores do usuário (sem a chave em claro; só `has_key`)."""
    out: list[dict[str, Any]] = []
    for it in await _items(db, str(user_id)):
        slug = it.get("slug") or ""
        out.append({
            "slug": slug,
            "name": it.get("name") or slug,
            "base_url": it.get("base_url") or "",
            "models": _parse_models(it.get("models")),
            "enabled": it.get("enabled", True) is not False,
            "has_key": await has_secret(db, user_id, _secret_name(slug)),
        })
    return out


async def upsert(
    db: AsyncSession, user_id, *,
    slug: str, name: str | None = None, base_url: str | None = None,
    models: Any = None, enabled: bool | None = None, api_key: str | None = None,
) -> dict[str, Any]:
    slug = _slugify(slug)
    items = await _items(db, str(user_id))
    cur = next((it for it in items if it.get("slug") == slug), None)
    if cur is None:
        cur = {"slug": slug, "name": name or slug, "base_url": "", "models": [], "enabled": True}
        items.append(cur)
    if name is not None:
        cur["name"] = name.strip() or slug
    if base_url is not None:
        cur["base_url"] = _norm_base(base_url)
    if models is not None:
        cur["models"] = _parse_models(models)
    if enabled is not None:
        cur["enabled"] = bool(enabled)
    await _save_items(db, str(user_id), items)
    # a chave vazia NÃO apaga a existente (o usuário pode salvar só nome/URL/modelos)
    if api_key is not None and api_key.strip():
        await set_secret(db, user_id, _secret_name(slug), api_key.strip())
    return {"slug": slug, "name": cur["name"], "base_url": cur["base_url"],
            "models": _parse_models(cur.get("models")), "enabled": cur["enabled"],
            "has_key": await has_secret(db, user_id, _secret_name(slug))}


async def delete(db: AsyncSession, user_id, slug: str) -> None:
    items = await _items(db, str(user_id))
    items = [it for it in items if it.get("slug") != slug]
    await _save_items(db, str(user_id), items)
    await delete_secret(db, user_id, _secret_name(slug))


async def resolve(db: AsyncSession, user_id, slug: str) -> dict[str, Any] | None:
    """Provedor EFETIVO ou None se desligado / sem base / sem chave. ``base_url`` vem
    como TEMPLATE (pode conter ``{model}``); use ``resolve_for_model`` p/ a URL final."""
    it = next((x for x in await _items(db, str(user_id)) if x.get("slug") == slug), None)
    if not it or it.get("enabled") is False:
        return None
    base = _norm_base(it.get("base_url") or "")
    if not base:
        return None
    key = await get_secret(db, user_id, _secret_name(slug))
    if not key:
        return None
    return {"base_url": base, "api_key": key, "name": it.get("name") or slug,
            "models": _parse_models(it.get("models"))}


async def resolve_for_model(db: AsyncSession, user_id, model: str) -> dict[str, Any] | None:
    """Resolve o provedor p/ um id ``@<slug>/<model_id>`` → base_url JÁ com ``{model}``
    substituído (kie.ai põe o modelo no caminho) + api_key. É o que o turno usa."""
    slug = slug_from_model(model)
    prov = await resolve(db, user_id, slug) if slug else None
    if not prov:
        return None
    base = _container_host(_apply_model(prov["base_url"], _real_model(model)))
    return {"base_url": base, "api_key": prov["api_key"], "name": prov["name"]}


async def list_models(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Modelos do provedor (`GET {base}/models`, formato OpenAI) → [{id, name}]."""
    base = _norm_base(base_url)
    if not base:
        return []
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_container_host(base)}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("data") if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for m in rows or []:
        mid = (m.get("id") or m.get("name") or "").strip() if isinstance(m, dict) else str(m).strip()
        if mid:
            out.append({"id": mid, "name": mid})
    out.sort(key=lambda x: x["name"].lower())
    return out


async def list_user_models(db: AsyncSession, user_id) -> list[dict[str, Any]]:
    """União dos modelos de TODOS os provedores ligados+com chave do usuário, no shape
    dos seletores: ``{id: '@<slug>/<mid>', name, provider}``.

    Fonte dos modelos: a LISTA MANUAL do provedor quando informada (obrigatória p/
    provedores que não expõem ``/models`` ou usam ``{model}`` no caminho, ex.: kie.ai);
    caso contrário tenta descobrir via ``GET {base}/models`` (LiteLLM & afins).
    Best-effort: um provedor offline é ignorado (não derruba os outros)."""
    out: list[dict[str, Any]] = []
    for it in await _items(db, str(user_id)):
        slug = it.get("slug") or ""
        prov = await resolve(db, user_id, slug)
        if not prov:
            continue
        ids = _parse_models(prov.get("models"))
        if not ids and "{model}" not in prov["base_url"]:
            try:
                ids = [m["id"] for m in await list_models(prov["base_url"], prov["api_key"])]
            except Exception as exc:  # noqa: BLE001
                logger.info("Provedor '%s' /models falhou (%s); ignorando", slug, exc)
                continue
        for mid in ids:
            out.append({
                "id": f"{MODEL_PREFIX}{slug}/{mid}",
                "name": mid,
                "provider": prov["name"],
            })
    return out


async def _probe_chat(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    """Valida chave+URL com uma completion mínima (max_tokens=1). Necessário p/
    provedores sem ``/models``. Também trata provedores que respondem HTTP 200 com
    ``{"code":401,...}`` no corpo em falha de auth (fora do padrão)."""
    url = _container_host(_apply_model(base_url, model)).rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 1, "stream": False},
        )
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:180]}"}
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": f"Resposta não-JSON: {resp.text[:120]}"}
    code = data.get("code") if isinstance(data, dict) else None
    if isinstance(code, int) and code >= 400:
        return {"ok": False, "error": f"{code}: {data.get('msg') or data.get('message') or 'erro'}"}
    return {"ok": True, "count": 1, "models": [model]}


async def test_connection(base_url: str, api_key: str, models: Any = None) -> dict[str, Any]:
    """Sonda a conexão. Se há modelos manuais OU ``{model}`` na base (kie.ai), valida via
    uma completion mínima no 1º modelo; senão lista via ``GET {base}/models`` (LiteLLM)."""
    ids = _parse_models(models)
    base = _norm_base(base_url)
    try:
        if ids or "{model}" in base:
            model = ids[0] if ids else "gpt-4o"
            return await _probe_chat(base, api_key, model)
        found = await list_models(base, api_key)
        return {"ok": True, "count": len(found), "models": [m["name"] for m in found[:20]]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}
