"""Voz: TTS (texto→áudio) e STT (áudio→texto).

Proxia para um endpoint compatível com OpenAI (/audio/speech, /audio/transcriptions).
O TTS pode usar uma **conexão de Voz Local por-usuário** (Kokoro-FastAPI ou um servidor
de clonagem OpenAI-compatível — ver `integrations/voice_service`); se não houver, cai no
provedor global (OpenAI via VOICE_BASE_URL + segredo voice_api_key). O STT continua no
provedor global (Kokoro não faz STT).
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .config import get_settings
from .db import get_db
from .integrations import voice_service
from .models import User
from .secrets_service import VOICE_KEY, get_secret

router = APIRouter(prefix="/voice", tags=["voice"])

# limites: evitam abuso de memória/custo (25MB = teto do Whisper; 4k chars ≈ teto TTS OpenAI)
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MAX_TTS_CHARS = 4096


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TTS_CHARS)
    voice: str | None = Field(default=None, max_length=120)  # aceita mistura ex.: "af_bella(2)+af_sky(1)"


class VoiceConfigIn(BaseModel):
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=400)
    tts_model: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None


async def _global_key(db: AsyncSession, user: User) -> str:
    key = await get_secret(db, user.id, VOICE_KEY)
    if not key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Configure a chave do provedor de voz (ou uma conexão de Voz Local) em Configurações",
        )
    return key


async def _resolve_tts(db: AsyncSession, user: User) -> tuple[str, str, str]:
    """(base_url, api_key, tts_model) do TTS: conexão local do usuário → provedor global."""
    prov = await voice_service.get_provider(db, user.id)
    if prov:
        return prov["base_url"], prov["api_key"], prov["tts_model"]
    s = get_settings()
    return s.voice_base_url, await _global_key(db, user), s.tts_model


@router.post("/tts")
async def tts(
    body: TTSIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    base_url, key, model = await _resolve_tts(db, user)
    voice = body.voice or get_settings().tts_voice
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "voice": voice,
                "input": body.text,
                "response_format": "mp3",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Falha no TTS: {resp.text[:200]}")
    return Response(content=resp.content, media_type="audio/mpeg")


@router.post("/stt")
async def stt(
    file: UploadFile = File(...),
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    s = get_settings()
    key = await _global_key(db, user)  # STT sempre no provedor global (whisper)
    # lê no máximo o limite + 1 byte para detectar estouro sem carregar tudo
    audio = await file.read(_MAX_AUDIO_BYTES + 1)
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Áudio excede o limite de {_MAX_AUDIO_BYTES // (1024 * 1024)}MB",
        )
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{s.voice_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (file.filename or "audio.webm", audio, file.content_type)},
            data={"model": s.stt_model},
        )
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Falha no STT: {resp.text[:200]}")
    return resp.json()


# --------------------------------------------------------------------------- #
# Conexão de Voz Local (Kokoro / servidor de clonagem OpenAI-compatível)
# --------------------------------------------------------------------------- #
@router.get("/config")
async def voice_config(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await voice_service.public_config(db, user.id)
    voices: list[str] = []
    if cfg["configured"] and cfg["enabled"]:
        voices = await voice_service.list_user_voices(db, user.id)
    return {**cfg, "voices": voices}


@router.put("/config")
async def voice_config_save(
    body: VoiceConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    await voice_service.set_config(
        db, user.id,
        base_url=body.base_url, api_key=body.api_key,
        tts_model=body.tts_model, enabled=body.enabled,
    )
    return await voice_config(user=user, db=db)


@router.get("/voices")
async def voice_list(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    """Vozes disponíveis na conexão local do usuário (para o seletor por-modelo)."""
    return {"voices": await voice_service.list_user_voices(db, user.id)}


@router.post("/test")
async def voice_test(
    body: VoiceConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    base = body.base_url or (await voice_service.public_config(db, user.id))["base_url"]
    return await voice_service.test_connection(base, body.api_key or "local")
