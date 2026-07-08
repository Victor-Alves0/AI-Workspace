"""Integração Ollama (modelos locais) — config POR-USUÁRIO.

O usuário aponta a URL do seu servidor Ollama (ex.: http://localhost:11434 ou o IP
da LAN). Guardado em app_settings sob `ollama:{user_id}`. NÃO há segredo (Ollama não
usa chave). O chat usa a API OpenAI-compatível do Ollama (`{base}/v1/chat/completions`),
então o MESMO streaming do OpenRouter serve — só muda a base_url.

Os modelos instalados (via `/api/tags`) entram nos seletores como `ollama/<nome>`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..app_config import get_setting, set_setting

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
# prefixo que identifica um modelo local do Ollama no id (distingue do OpenRouter)
MODEL_PREFIX = "ollama/"


def _key(user_id: str) -> str:
    return f"ollama:{user_id}"


def _norm_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "http://" + u
    return u


async def get_base_url(db: AsyncSession, user_id) -> str | None:
    """Base URL configurada (normalizada) ou None se não configurado/ desligado."""
    raw = await get_setting(db, _key(str(user_id)))
    if not isinstance(raw, dict) or raw.get("enabled") is False:
        return None
    b = _norm_base(raw.get("base_url") or "")
    return b or None


async def set_config(db: AsyncSession, user_id, *, base_url: str | None = None, enabled: bool | None = None) -> None:
    cur = await get_setting(db, _key(str(user_id)))
    out = dict(cur) if isinstance(cur, dict) else {}
    if base_url is not None:
        out["base_url"] = _norm_base(base_url)
    if enabled is not None:
        out["enabled"] = bool(enabled)
    await set_setting(db, _key(str(user_id)), out)


async def public_config(db: AsyncSession, user_id) -> dict[str, Any]:
    raw = await get_setting(db, _key(str(user_id)))
    raw = raw if isinstance(raw, dict) else {}
    base = _norm_base(raw.get("base_url") or "")
    return {
        "configured": bool(base),
        "base_url": base or DEFAULT_BASE_URL,
        "enabled": raw.get("enabled", True) is not False,
    }


async def list_models(base_url: str) -> list[dict[str, Any]]:
    """Modelos instalados no Ollama (`GET /api/tags`), no formato dos seletores:
    `{id: 'ollama/<nome>', name, local: true, ...}`."""
    base = _norm_base(base_url)
    if not base:
        return []
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{base}/api/tags")
        resp.raise_for_status()
        data = resp.json()
    out: list[dict[str, Any]] = []
    for m in data.get("models", []) or []:
        name = (m.get("name") or m.get("model") or "").strip()
        if not name:
            continue
        details = m.get("details") or {}
        out.append({
            "id": f"{MODEL_PREFIX}{name}",
            "name": name,
            "local": True,
            "size": m.get("size"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


async def list_user_models(db: AsyncSession, user_id) -> list[dict[str, Any]]:
    """Modelos do Ollama do usuário (best-effort: [] se não configurado/ offline)."""
    base = await get_base_url(db, user_id)
    if not base:
        return []
    try:
        return await list_models(base)
    except Exception as exc:  # noqa: BLE001
        logger.info("Ollama /api/tags falhou (%s); sem modelos locais", exc)
        return []


async def test_connection(base_url: str) -> dict[str, Any]:
    try:
        models = await list_models(base_url)
        return {"ok": True, "count": len(models), "models": [m["name"] for m in models[:20]]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
