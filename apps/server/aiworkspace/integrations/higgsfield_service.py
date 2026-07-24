"""Higgsfield: geração de imagem e vídeo (Soul, Seedream, FLUX, DoP, Kling…).

Conexão POR-USUÁRIO (como a Tuya), em `app_settings` sob `higgsfield:{user_id}`
(secret cifrado): {api_key, api_secret_enc}. Credenciais vêm de
cloud.higgsfield.ai → API Keys.

Contrato da API (docs.higgsfield.ai, verificado também no SDK oficial):
  - POST https://platform.higgsfield.ai/{model_id}  body = argumentos JSON
    → {request_id, status_url, cancel_url}
  - Auth: `Authorization: Key {api_key}:{api_secret}`
  - GET /requests/{id}/status → {status: queued|in_progress|completed|failed|
    nsfw|canceled, ...}; quando completed, o MESMO endpoint carrega o resultado
    (images: [{url}] ou video: {url}).
  - Upload de referência: POST /files/generate-upload-url {content_type}
    → {public_url, upload_url}; PUT dos bytes na upload_url.

Chamadas HTTP SÍNCRONAS (httpx.Client): a tool SIFT roda no threadpool de
dispatch — igual à Tuya. A espera é um poll com teto; se estourar, a tool
devolve o request_id e a ação `status` retoma de onde parou.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..app_config import get_setting, set_setting

logger = logging.getLogger(__name__)

BASE_URL = "https://platform.higgsfield.ai"

# Catálogo dos modelos do backend oficial (docs + SDK). `params` lista o que
# cada um aceita além de `prompt` — a tool repassa só o que o modelo suporta,
# para um argumento inválido não derrubar a requisição inteira.
IMAGE_MODELS: dict[str, dict[str, Any]] = {
    "higgsfield-ai/soul/standard": {
        "label": "Soul (Higgsfield)", "params": ["aspect_ratio", "resolution"],
        "note": "flagship photorealistic text-to-image",
    },
    "reve/text-to-image": {
        "label": "Reve", "params": ["aspect_ratio", "resolution"],
        "note": "versatile text-to-image",
    },
    "bytedance/seedream/v4/text-to-image": {
        "label": "Seedream v4", "params": ["aspect_ratio", "resolution"],
        "note": "text-to-image; resolutions up to 2K/4K",
    },
    "bytedance/seedream/v4/edit": {
        "label": "Seedream v4 Edit", "params": ["image_url", "aspect_ratio", "resolution"],
        "note": "edit an existing image with a prompt (needs image_url)",
    },
    "flux-pro/kontext/max/text-to-image": {
        "label": "FLUX.1 Kontext Max", "params": ["aspect_ratio", "seed"],
        "note": "text-to-image with strong prompt adherence",
    },
}
VIDEO_MODELS: dict[str, dict[str, Any]] = {
    "higgsfield-ai/dop/standard": {
        "label": "DoP (Higgsfield)", "params": ["image_url", "duration"],
        "note": "image-to-video, cinematic motion",
    },
    "higgsfield-ai/dop/preview": {
        "label": "DoP Preview", "params": ["image_url", "duration"],
        "note": "image-to-video, faster/cheaper preview",
    },
    "bytedance/seedance/v1/pro/image-to-video": {
        "label": "Seedance v1 Pro", "params": ["image_url", "duration"],
        "note": "image-to-video",
    },
    "kling-video/v2.1/pro/image-to-video": {
        "label": "Kling v2.1 Pro", "params": ["image_url", "duration"],
        "note": "image-to-video",
    },
}
DEFAULT_IMAGE_MODEL = "higgsfield-ai/soul/standard"
DEFAULT_VIDEO_MODEL = "higgsfield-ai/dop/standard"

_TIMEOUT = 30.0  # por requisição HTTP (o poll soma várias)


# --------------------------------------------------------------------------- #
# Conexão por-usuário (app_settings)
# --------------------------------------------------------------------------- #
def _key(user_id: str) -> str:
    return f"higgsfield:{user_id}"


async def get_config(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    """{api_key, api_secret} decifrado, ou None se não conectado."""
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict) or not raw.get("api_key"):
        return None
    try:
        secret = crypto.decrypt(raw.get("api_secret_enc") or "")
    except Exception:  # noqa: BLE001 - APP_SECRET trocado sem migrar
        logger.warning("higgsfield: secret indecifrável (APP_SECRET mudou?)")
        return None
    return {"api_key": raw["api_key"], "api_secret": secret}


async def set_config(db: AsyncSession, user_id: str, api_key: str, api_secret: str) -> None:
    """Grava a conexão. `api_secret` vazio preserva o secret já salvo (edição só
    da key não força redigitar o secret)."""
    cur = await get_setting(db, _key(user_id))
    out: dict[str, Any] = {"api_key": api_key.strip()}
    if api_secret.strip():
        out["api_secret_enc"] = crypto.encrypt(api_secret.strip())
    elif isinstance(cur, dict) and cur.get("api_secret_enc"):
        out["api_secret_enc"] = cur["api_secret_enc"]
    await set_setting(db, _key(user_id), out)


async def delete_config(db: AsyncSession, user_id: str) -> None:
    await set_setting(db, _key(user_id), None)


async def public_config(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Config sem segredos, p/ a UI de Conexões."""
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict) or not raw.get("api_key"):
        return {"connected": False}
    key = str(raw["api_key"])
    return {"connected": True, "api_key_masked": key[:4] + "…" + key[-4:] if len(key) > 8 else "…"}


async def is_configured(db: AsyncSession, user_id: str) -> bool:
    raw = await get_setting(db, _key(user_id))
    return isinstance(raw, dict) and bool(raw.get("api_key"))


# --------------------------------------------------------------------------- #
# Cliente HTTP (sync — roda no threadpool das tools)
# --------------------------------------------------------------------------- #
def _headers(conn: dict) -> dict[str, str]:
    return {
        "Authorization": f"Key {conn['api_key']}:{conn['api_secret']}",
        "Content-Type": "application/json",
    }


def test_connection(conn: dict) -> dict[str, Any]:
    """Sonda as credenciais sem gastar créditos: GET num request inexistente.
    404 = autenticou (só não achou o id); 401/403 = credencial ruim."""
    try:
        r = httpx.get(
            f"{BASE_URL}/requests/{uuid.uuid4()}/status",
            headers=_headers(conn), timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"sem conexão com a Higgsfield: {exc}"}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "credenciais inválidas (verifique key e secret)"}
    return {"ok": True}


def submit(conn: dict, application: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """POST /{model_id}. Devolve {request_id, ...} ou {error}."""
    try:
        r = httpx.post(
            f"{BASE_URL}/{application.strip('/')}",
            headers=_headers(conn), json=arguments, timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao enviar à Higgsfield: {exc}"}
    if r.status_code in (401, 403):
        return {"error": "Higgsfield: credenciais inválidas"}
    if r.status_code >= 400:
        return {"error": f"Higgsfield HTTP {r.status_code}: {r.text[:300]}"}
    data = r.json()
    if not data.get("request_id"):
        return {"error": f"resposta inesperada da Higgsfield: {str(data)[:200]}"}
    return data


def fetch_status(conn: dict, request_id: str) -> dict[str, Any]:
    """GET /requests/{id}/status — no estado completed carrega o resultado."""
    try:
        r = httpx.get(
            f"{BASE_URL}/requests/{request_id}/status",
            headers=_headers(conn), timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao consultar a Higgsfield: {exc}"}
    if r.status_code == 404:
        return {"error": f"request '{request_id}' não encontrado"}
    if r.status_code >= 400:
        return {"error": f"Higgsfield HTTP {r.status_code}: {r.text[:300]}"}
    return r.json()


def wait(conn: dict, request_id: str, budget_s: float, poll_s: float = 3.0) -> dict[str, Any]:
    """Poll até terminar ou estourar o orçamento. Devolve o último status visto
    (quem chama decide o que fazer com in_progress/queued no estouro)."""
    deadline = time.monotonic() + budget_s
    last: dict[str, Any] = {"status": "queued"}
    while time.monotonic() < deadline:
        last = fetch_status(conn, request_id)
        if last.get("error") or last.get("status") in ("completed", "failed", "nsfw", "canceled"):
            return last
        time.sleep(poll_s)
    return last


def upload_bytes(conn: dict, data: bytes, content_type: str) -> str | dict[str, Any]:
    """Sobe bytes (imagem de referência) e devolve a public_url — ou {error}.
    Necessário quando a referência mora no NOSSO servidor (URL de LAN que a
    Higgsfield não alcança)."""
    try:
        r = httpx.post(
            f"{BASE_URL}/files/generate-upload-url",
            headers=_headers(conn), json={"content_type": content_type}, timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            return {"error": f"Higgsfield upload HTTP {r.status_code}: {r.text[:200]}"}
        j = r.json()
        put = httpx.put(
            j["upload_url"], content=data,
            headers={"Content-Type": content_type}, timeout=120.0,
        )
        if put.status_code >= 400:
            return {"error": f"upload falhou (HTTP {put.status_code})"}
        return j["public_url"]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"upload à Higgsfield falhou: {exc}"}


def download(url: str, max_bytes: int = 100 * 1024 * 1024) -> tuple[bytes, str] | dict[str, Any]:
    """Baixa o resultado (CDN da Higgsfield) → (bytes, mime) ou {error}. As URLs
    deles expiram; guardamos os bytes no banco como qualquer imagem gerada."""
    try:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as r:
            if r.status_code >= 400:
                return {"error": f"download do resultado falhou (HTTP {r.status_code})"}
            mime = r.headers.get("content-type", "application/octet-stream").split(";")[0]
            chunks: list[bytes] = []
            total = 0
            for chunk in r.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    return {"error": "resultado grande demais (>100MB)"}
                chunks.append(chunk)
            return b"".join(chunks), mime
    except Exception as exc:  # noqa: BLE001
        return {"error": f"download do resultado falhou: {exc}"}
