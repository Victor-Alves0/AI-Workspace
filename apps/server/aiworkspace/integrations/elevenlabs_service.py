"""ElevenLabs: TTS premium, clonagem de voz e efeitos sonoros.

Conexão POR-USUÁRIO (como a Higgsfield), em `app_settings` sob `elevenlabs:{user_id}`
(api key cifrada): {api_key_enc, model, default_voice, enabled}. Serve DOIS papéis:

  1. Provedor de VOZ DO SISTEMA: quando conectado+ligado, o `/voice/tts` sintetiza as
     respostas faladas com vozes ElevenLabs (voz por-modelo escolhida com o prefixo
     `el:` no seletor). A ElevenLabs NÃO é OpenAI-compat (usa `xi-api-key` e
     `/text-to-speech/{voice_id}`), por isso tem serviço próprio em vez de entrar na
     Voz Local.
  2. Ferramenta de ÁUDIO no chat: a tool `elevenlabs.audio.generate` gera fala de
     qualquer texto e efeitos sonoros como artefato de áudio.

As chamadas são SÍNCRONAS (httpx.Client) — a tool roda no threadpool de dispatch e as
rotas async chamam via run_in_threadpool. `EL_VOICE_PREFIX` marca as vozes ElevenLabs
nos seletores de voz (que hoje listam as vozes locais como strings simples).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..app_config import get_setting, set_setting

logger = logging.getLogger(__name__)

BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
# voz pública padrão (Rachel) — usada quando o modelo não escolheu nenhuma.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
# prefixo que marca uma voz ElevenLabs nos seletores (as locais são strings simples).
EL_VOICE_PREFIX = "el:"
_TIMEOUT = 60.0        # síntese (TTS/efeito) — pode demorar
_LIST_TIMEOUT = 15.0   # listar vozes / testar — não pode travar o load das Configurações
# cache curto das vozes por chave (evita um GET por síntese e sobrevive a blips).
_VOICES_TTL = 300.0
_voices_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _key(user_id: str) -> str:
    return f"elevenlabs:{user_id}"


# --------------------------------------------------------------------------- #
# Conexão por-usuário (app_settings)
# --------------------------------------------------------------------------- #
async def get_config(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    """{api_key, model, default_voice, enabled} decifrado, ou None se não conectado."""
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict) or not raw.get("api_key_enc"):
        return None
    try:
        api_key = crypto.decrypt(raw["api_key_enc"])
    except Exception:  # noqa: BLE001 - APP_SECRET trocado sem migrar
        logger.warning("elevenlabs: api key indecifrável (APP_SECRET mudou?)")
        return None
    return {
        "api_key": api_key,
        "model": raw.get("model") or DEFAULT_MODEL,
        "default_voice": raw.get("default_voice") or DEFAULT_VOICE,
        "enabled": raw.get("enabled", True) is not False,
    }


async def get_provider(db: AsyncSession, user_id: str) -> dict[str, Any] | None:
    """Conexão EFETIVA (só se ligada), p/ o `/voice/tts` preferir a ElevenLabs."""
    cfg = await get_config(db, user_id)
    if not cfg or not cfg.get("enabled"):
        return None
    return cfg


async def set_config(
    db: AsyncSession, user_id: str, *,
    api_key: str | None = None, model: str | None = None,
    default_voice: str | None = None, enabled: bool | None = None,
) -> None:
    """Grava a conexão. `api_key` vazio preserva a já salva (editar sem redigitar)."""
    cur = await get_setting(db, _key(user_id))
    out: dict[str, Any] = dict(cur) if isinstance(cur, dict) else {}
    if api_key and api_key.strip():
        out["api_key_enc"] = crypto.encrypt(api_key.strip())
    if model is not None:
        out["model"] = model.strip() or DEFAULT_MODEL
    if default_voice is not None:
        out["default_voice"] = default_voice.strip()
    if enabled is not None:
        out["enabled"] = bool(enabled)
    await set_setting(db, _key(user_id), out)


async def delete_config(db: AsyncSession, user_id: str) -> None:
    await set_setting(db, _key(user_id), None)


async def public_config(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Config sem segredos, p/ a UI de Conexões."""
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict) or not raw.get("api_key_enc"):
        return {"connected": False, "enabled": False, "model": DEFAULT_MODEL, "default_voice": ""}
    return {
        "connected": True,
        "enabled": raw.get("enabled", True) is not False,
        "model": raw.get("model") or DEFAULT_MODEL,
        "default_voice": raw.get("default_voice") or "",
    }


async def is_configured(db: AsyncSession, user_id: str) -> bool:
    raw = await get_setting(db, _key(user_id))
    return isinstance(raw, dict) and bool(raw.get("api_key_enc"))


# --------------------------------------------------------------------------- #
# Web API (SÍNCRONO — threadpool das tools / run_in_threadpool nas rotas)
# --------------------------------------------------------------------------- #
def _headers(api_key: str) -> dict[str, str]:
    return {"xi-api-key": api_key, "Content-Type": "application/json"}


def test_connection(api_key: str) -> dict[str, Any]:
    """Sonda a chave: GET /v1/voices. 200 = ok; 401 = chave ruim."""
    try:
        r = httpx.get(f"{BASE_URL}/v1/voices", headers={"xi-api-key": api_key}, timeout=_LIST_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"sem conexão com a ElevenLabs: {exc}"}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "chave inválida"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"ElevenLabs HTTP {r.status_code}"}
    return {"ok": True}


def list_voices(api_key: str, *, use_cache: bool = True) -> list[dict[str, str]]:
    """Vozes disponíveis na conta → [{id, name}]. Cacheado por chave (TTL curto):
    numa falha transitória, serve o cache anterior em vez de sumir com as vozes."""
    if not api_key:
        return []
    import time as _time
    now = _time.monotonic()
    hit = _voices_cache.get(api_key)
    if use_cache and hit is not None and now - hit[0] < _VOICES_TTL:
        return hit[1]
    try:
        r = httpx.get(f"{BASE_URL}/v1/voices", headers={"xi-api-key": api_key}, timeout=_LIST_TIMEOUT)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.info("ElevenLabs list_voices falhou: %s", exc)
        return hit[1] if hit is not None else []  # serve o cache anterior, se houver
    out = []
    for v in (r.json().get("voices") or []):
        if isinstance(v, dict) and v.get("voice_id"):
            out.append({"id": v["voice_id"], "name": v.get("name") or v["voice_id"]})
    _voices_cache[api_key] = (now, out)
    return out


def resolve_voice(api_key: str, voice: str) -> str:
    """Aceita um voice_id, um nome, ou 'el:<algo>' → devolve o voice_id. Se for um
    nome, resolve pela lista; sem match, devolve o que veio (pode já ser um id)."""
    v = (voice or "").strip()
    if v.startswith(EL_VOICE_PREFIX):
        v = v[len(EL_VOICE_PREFIX):].strip()
    if not v:
        return DEFAULT_VOICE
    for entry in list_voices(api_key):
        if v == entry["id"] or v.lower() == entry["name"].lower():
            return entry["id"]
    return v


def tts(api_key: str, text: str, voice: str = "", model: str = "") -> tuple[bytes, str]:
    """Sintetiza fala. Devolve (bytes mp3, mime) ou levanta RuntimeError."""
    voice_id = resolve_voice(api_key, voice) if voice else DEFAULT_VOICE
    try:
        r = httpx.post(
            f"{BASE_URL}/v1/text-to-speech/{voice_id}",
            headers=_headers(api_key),
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": model or DEFAULT_MODEL},
            timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"falha ao falar com a ElevenLabs: {exc}") from exc
    if r.status_code in (401, 403):
        raise RuntimeError("ElevenLabs: chave inválida")
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs TTS HTTP {r.status_code}: {r.text[:200]}")
    return r.content, "audio/mpeg"


def sound_effect(api_key: str, text: str, duration: float | None = None) -> tuple[bytes, str]:
    """Gera um efeito sonoro a partir de uma descrição. (bytes mp3, mime) ou erro."""
    body: dict[str, Any] = {"text": text}
    if duration:
        body["duration_seconds"] = max(0.5, min(float(duration), 22.0))
    try:
        r = httpx.post(
            f"{BASE_URL}/v1/sound-generation",
            headers=_headers(api_key), json=body, timeout=_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"falha ao gerar efeito na ElevenLabs: {exc}") from exc
    if r.status_code in (401, 403):
        raise RuntimeError("ElevenLabs: chave inválida")
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs SFX HTTP {r.status_code}: {r.text[:200]}")
    return r.content, "audio/mpeg"
