"""Voz LOCAL (TTS) — config POR-USUÁRIO.

O usuário aponta a URL de um servidor de voz compatível com OpenAI (ex.: Kokoro-FastAPI
em http://localhost:8880/v1, ou um servidor de clonagem que exponha /audio/speech).
Guardado em app_settings sob `voice:{user_id}`. Quando ligado, o `/voice/tts` roteia
para esse servidor em vez do provedor global (OpenAI via env).

Espelha a integração Ollama ([[ollama-integration]]): mesma ideia, só que para voz.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..app_config import get_setting, set_setting

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:8880/v1"
DEFAULT_TTS_MODEL = "kokoro"

# Vozes preset do Kokoro (fallback quando o servidor não expõe /audio/voices).
# a=American, b=British; f=female, m=male. O Kokoro-FastAPI aceita mistura com
# pesos, ex.: "af_bella(2)+af_sky(1)".
FALLBACK_KOKORO_VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_nova", "af_aoede", "af_kore",
    "am_adam", "am_michael", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_onyx", "am_puck",
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily", "bm_george", "bm_lewis", "bm_daniel", "bm_fable",
]


def _key(user_id: str) -> str:
    return f"voice:{user_id}"


def _norm_base(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        u = "http://" + u
    return u


def _container_host(u: str) -> str:
    """Dentro do container, o "localhost" digitado pelo usuário é a MÁQUINA dele,
    não o container — reescreve p/ host.docker.internal (Docker Desktop). Aplicado
    só na hora de USAR (o config guarda o que o usuário digitou)."""
    import os
    import re
    if os.path.exists("/.dockerenv"):
        return re.sub(r"//(localhost|127\.0\.0\.1)(?=[:/]|$)", "//host.docker.internal", u, count=1)
    return u


async def get_config(db: AsyncSession, user_id) -> dict[str, Any]:
    raw = await get_setting(db, _key(str(user_id)))
    return dict(raw) if isinstance(raw, dict) else {}


async def get_provider(db: AsyncSession, user_id) -> dict[str, Any] | None:
    """Provedor de voz local EFETIVO (base_url/api_key/tts_model) ou None se
    desligado/ não configurado (aí o `/voice/tts` cai no provedor global do env)."""
    raw = await get_config(db, user_id)
    if not raw or raw.get("enabled") is False:
        return None
    base = _norm_base(raw.get("base_url") or "")
    if not base:
        return None
    return {
        "base_url": _container_host(base),
        "api_key": raw.get("api_key") or "local",  # muitos servidores locais ignoram a chave
        "tts_model": (raw.get("tts_model") or DEFAULT_TTS_MODEL).strip(),
    }


async def set_config(
    db: AsyncSession, user_id, *,
    base_url: str | None = None, api_key: str | None = None,
    tts_model: str | None = None, enabled: bool | None = None,
) -> None:
    cur = await get_config(db, user_id)
    out = dict(cur)
    if base_url is not None:
        out["base_url"] = _norm_base(base_url)
    if api_key is not None:
        out["api_key"] = api_key.strip()
    if tts_model is not None:
        out["tts_model"] = tts_model.strip()
    if enabled is not None:
        out["enabled"] = bool(enabled)
    await set_setting(db, _key(str(user_id)), out)


async def public_config(db: AsyncSession, user_id) -> dict[str, Any]:
    raw = await get_config(db, user_id)
    base = _norm_base(raw.get("base_url") or "")
    return {
        "configured": bool(base),
        "base_url": base or DEFAULT_BASE_URL,
        "tts_model": raw.get("tts_model") or DEFAULT_TTS_MODEL,
        "has_key": bool(raw.get("api_key")),
        "enabled": raw.get("enabled", True) is not False,
    }


def _norm_voices(data: Any) -> list[str]:
    """Extrai a lista de vozes de vários formatos possíveis do servidor."""
    items = data
    if isinstance(data, dict):
        items = data.get("voices") or data.get("data") or []
    out: list[str] = []
    for v in items or []:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            name = v.get("id") or v.get("name") or v.get("voice")
            if name:
                out.append(str(name))
    return out


async def list_voices(base_url: str, api_key: str = "local") -> list[str]:
    """Vozes disponíveis no servidor (`GET {base}/audio/voices`). Se o servidor não
    expõe o endpoint, cai no conjunto preset do Kokoro."""
    base = _norm_base(base_url)
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{base}/audio/voices",
                headers={"Authorization": f"Bearer {api_key or 'local'}"},
            )
            resp.raise_for_status()
            voices = _norm_voices(resp.json())
            if voices:
                return sorted(voices)
    except Exception as exc:  # noqa: BLE001
        logger.info("Voz local /audio/voices falhou (%s); usando fallback Kokoro", exc)
    return list(FALLBACK_KOKORO_VOICES)


async def list_user_voices(db: AsyncSession, user_id) -> list[str]:
    prov = await get_provider(db, user_id)
    if not prov:
        return []
    return await list_voices(prov["base_url"], prov["api_key"])


async def test_connection(base_url: str, api_key: str = "local") -> dict[str, Any]:
    try:
        voices = await list_voices(base_url, api_key)
        return {"ok": True, "count": len(voices), "voices": voices[:30]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
