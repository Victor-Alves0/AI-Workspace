"""Voz: TTS (texto→áudio) e STT (áudio→texto).

Proxia para um endpoint compatível com OpenAI (/audio/speech, /audio/transcriptions).
O TTS pode usar uma **conexão de Voz Local por-usuário** (Kokoro-FastAPI ou um servidor
de clonagem OpenAI-compatível — ver `integrations/voice_service`); se não houver, cai no
provedor global (OpenAI via VOICE_BASE_URL + segredo voice_api_key). O STT continua no
provedor global (Kokoro não faz STT).
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .config import get_settings
from .db import get_db
from .integrations import elevenlabs_service, voice_service
from .models import Chat, Folder, ModelConfig, User
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
    # Voz ElevenLabs (voz por-modelo prefixada "el:"): sintetiza pela API nativa da
    # ElevenLabs (não é OpenAI-compat), antes do caminho Voz Local/global.
    if (body.voice or "").startswith(elevenlabs_service.EL_VOICE_PREFIX):
        conn = await elevenlabs_service.get_provider(db, str(user.id))
        if conn is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Voz ElevenLabs selecionada, mas a conexão ElevenLabs está desligada ou não configurada.",
            )
        try:
            data, mime = await run_in_threadpool(
                elevenlabs_service.tts, conn["api_key"], body.text, body.voice, conn["model"]
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha no TTS ElevenLabs: {exc}") from None
        return Response(content=data, media_type=mime)

    base_url, key, model = await _resolve_tts(db, user)
    voice = body.voice or get_settings().tts_voice
    try:
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
    except httpx.HTTPError:
        # servidor de voz inacessível (ex.: conexão local configurada mas desligada):
        # erro claro em vez de 500 — o front cai na voz do navegador
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Servidor de voz inacessível ({base_url}). Ele está rodando?",
        ) from None
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Falha no TTS: {resp.text[:200]}")
    return Response(content=resp.content, media_type="audio/mpeg")


async def _try_transcribe(base_url: str, key: str, model: str, filename: str, audio: bytes, mime: str):
    """Uma tentativa de transcrição OpenAI-compat. Retorna o JSON ou None quando o
    servidor não faz STT (404/405/501 — ex.: Kokoro só faz TTS) ou está fora do ar."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, mime)},
                data={"model": model},
            )
    except httpx.HTTPError:
        return None
    if resp.status_code in (404, 405, 501):
        return None
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Falha no STT: {resp.text[:200]}")
    return resp.json()


@router.post("/stt")
async def stt(
    file: UploadFile = File(...),
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    s = get_settings()
    # lê no máximo o limite + 1 byte para detectar estouro sem carregar tudo
    audio = await file.read(_MAX_AUDIO_BYTES + 1)
    if len(audio) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Áudio excede o limite de {_MAX_AUDIO_BYTES // (1024 * 1024)}MB",
        )
    fname = file.filename or "audio.webm"
    mime = file.content_type or "audio/webm"
    # 1º a conexão de voz LOCAL do usuário (stacks como speaches/faster-whisper
    # expõem /audio/transcriptions; Kokoro devolve 404 e caímos adiante)…
    prov = await voice_service.get_provider(db, user.id)
    if prov:
        out = await _try_transcribe(prov["base_url"], prov["api_key"], s.stt_model, fname, audio, mime)
        if out is not None:
            return out
    # …depois o provedor global (exige a chave)
    key = await get_secret(db, user.id, VOICE_KEY)
    if not key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nenhum servidor de transcrição disponível: a conexão de Voz Local não faz "
            "STT e não há chave do provedor de voz configurada.",
        )
    out = await _try_transcribe(s.voice_base_url, key, s.stt_model, fname, audio, mime)
    if out is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Provedor de voz global inacessível")
    return out


# --------------------------------------------------------------------------- #
# Conexão de Voz Local (Kokoro / servidor de clonagem OpenAI-compatível)
# --------------------------------------------------------------------------- #
async def _elevenlabs_voice_names(db: AsyncSession, user: User) -> list[str]:
    """Vozes ElevenLabs do usuário como strings 'el:<nome>' p/ o seletor por-modelo
    (que hoje lista vozes como strings). Vazio se desligada/não configurada."""
    conn = await elevenlabs_service.get_provider(db, str(user.id))
    if conn is None:
        return []
    voices = await run_in_threadpool(elevenlabs_service.list_voices, conn["api_key"])
    return [f"{elevenlabs_service.EL_VOICE_PREFIX}{v['name']}" for v in voices]


@router.get("/config")
async def voice_config(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await voice_service.public_config(db, user.id)
    voices: list[str] = []
    if cfg["configured"] and cfg["enabled"]:
        voices = await voice_service.list_user_voices(db, user.id)
    # ElevenLabs (se ligada) entra no MESMO seletor, prefixada "el:".
    voices = [*voices, *await _elevenlabs_voice_names(db, user)]
    return {**cfg, "voices": voices, "configured": cfg["configured"] or bool(voices)}


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
    """Vozes disponíveis (conexão de Voz Local + ElevenLabs) p/ o seletor por-modelo."""
    local = await voice_service.list_user_voices(db, user.id)
    return {"voices": [*local, *await _elevenlabs_voice_names(db, user)]}


@router.post("/test")
async def voice_test(
    body: VoiceConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    base = body.base_url or (await voice_service.public_config(db, user.id))["base_url"]
    return await voice_service.test_connection(base, body.api_key or "local")


# --------------------------------------------------------------------------- #
# Modo voz (assistente): resolve o chat que o modo voz usa para um modelo.
# `filter_config.listen`: {enabled, call_name, chat_mode: fixed|new, folder,
# auto_speak, hands_free, continuous, follow_up_secs}. "fixed" reusa um chat contínuo numa pasta
# `<folder>/<modelo>` (mesmo padrão dos canais); "new" abre um chat limpo.
# --------------------------------------------------------------------------- #
_VOICE_CHAT_TITLE = "Modo voz"


class VoiceSessionIn(BaseModel):
    model_config_id: str = Field(max_length=64)


async def _find_or_create_folder(db: AsyncSession, user_id, name: str, parent_id):
    f = await db.scalar(
        select(Folder).where(
            Folder.user_id == user_id, Folder.name == name, Folder.parent_id == parent_id
        )
    )
    if f is None:
        f = Folder(user_id=user_id, name=name, parent_id=parent_id)
        db.add(f)
        await db.flush()
    return f


@router.post("/session")
async def voice_session(
    body: VoiceSessionIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    try:
        mcid = uuid.UUID(body.model_config_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "model_config_id inválido")
    mc = await db.get(ModelConfig, mcid)
    if mc is None or mc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modelo não encontrado")
    listen = (mc.filter_config or {}).get("listen") or {}
    if not listen.get("enabled"):
        raise HTTPException(status.HTTP_409_CONFLICT, "O assistente de voz não está ligado neste modelo")

    mode = "new" if listen.get("chat_mode") == "new" else "fixed"
    chat = None
    if mode == "fixed":
        root_name = (str(listen.get("folder") or "Assistente").strip() or "Assistente")[:120]
        root = await _find_or_create_folder(db, user.id, root_name, None)
        sub = await _find_or_create_folder(db, user.id, (getattr(mc, "name", None) or "Modelo")[:120], root.id)
        chat = await db.scalar(
            select(Chat).where(
                Chat.user_id == user.id, Chat.model_config_id == mc.id,
                Chat.folder_id == sub.id, Chat.archived.is_(False),
            ).order_by(Chat.updated_at.desc()).limit(1)
        )
        if chat is None:
            chat = Chat(user_id=user.id, title=_VOICE_CHAT_TITLE, model=mc.base_model,
                        model_config_id=mc.id, params=mc.params or {}, folder_id=sub.id)
            db.add(chat)
            await db.flush()
    else:
        chat = Chat(user_id=user.id, title=_VOICE_CHAT_TITLE, model=mc.base_model,
                    model_config_id=mc.id, params=mc.params or {})
        db.add(chat)
        await db.flush()

    await db.commit()
    return {
        "chat_id": str(chat.id),
        "model": mc.base_model,
        "model_config_id": str(mc.id),
        "auto_speak": bool(listen.get("auto_speak", True)),
        "hands_free": bool(listen.get("hands_free", False)),
        "continuous": bool(listen.get("continuous", False)),
        # janela de graça (s) da conversa contínua; clamp defensivo 2..30
        "follow_up_secs": max(2, min(30, int(listen.get("follow_up_secs") or 8))),
        "call_name": str(listen.get("call_name") or ""),
    }
