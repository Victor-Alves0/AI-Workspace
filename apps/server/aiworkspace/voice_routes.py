"""Voz: TTS (texto→áudio) e STT (áudio→texto).

Proxia para um endpoint compatível com OpenAI (/audio/speech, /audio/transcriptions).
O TTS pode usar uma **conexão de Voz Local por-usuário** (Kokoro-FastAPI ou um servidor
de clonagem OpenAI-compatível — ver `integrations/voice_service`); se não houver, cai no
provedor global (OpenAI via VOICE_BASE_URL + segredo voice_api_key). O STT continua no
provedor global (Kokoro não faz STT).
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
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
from .providers import openrouter
from .secrets_service import OPENROUTER_KEY, VOICE_KEY, WAKE_CONFIG_KEY, get_secret, set_secret

router = APIRouter(prefix="/voice", tags=["voice"])

# limites: evitam abuso de memória/custo (25MB = teto do Whisper; 4k chars ≈ teto TTS OpenAI)
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MAX_TTS_CHARS = 4096


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TTS_CHARS)
    voice: str | None = Field(default=None, max_length=120)  # aceita mistura ex.: "af_bella(2)+af_sky(1)"
    model_config_id: uuid.UUID | None = None
    # Overrides são usados pelo botão Testar antes de salvar o modelo.
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=255)


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


async def _model_voice_config(
    db: AsyncSession, user: User, model_config_id: uuid.UUID | None
) -> dict:
    if model_config_id is None:
        return {}
    mc = await db.scalar(
        select(ModelConfig).where(ModelConfig.id == model_config_id, ModelConfig.user_id == user.id)
    )
    if mc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modelo não encontrado")
    raw = (mc.filter_config or {}).get("voice") or {}
    return raw if isinstance(raw, dict) else {}


async def _resolve_tts(
    db: AsyncSession,
    user: User,
    *,
    provider: str = "auto",
    model: str | None = None,
) -> tuple[str, str, str]:
    """Resolve (base_url, api_key, modelo) para a rota escolhida no modelo."""
    s = get_settings()
    if provider == "openrouter":
        key = await get_secret(db, user.id, OPENROUTER_KEY)
        if not key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configure sua chave do OpenRouter primeiro")
        return s.openrouter_base_url, key, model or "openai/gpt-4o-mini-tts-2025-12-15"
    if provider == "api":
        return s.voice_base_url, await _global_key(db, user), model or s.tts_model
    prov = await voice_service.get_provider(db, user.id)
    if provider == "local":
        if not prov:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configure e ative o servidor de voz local primeiro")
        return prov["base_url"], prov["api_key"], model or prov["tts_model"]
    if provider != "auto":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provedor de voz inválido")
    if prov:
        return prov["base_url"], prov["api_key"], model or prov["tts_model"]
    return s.voice_base_url, await _global_key(db, user), model or s.tts_model


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

    voice_cfg = await _model_voice_config(db, user, body.model_config_id)
    provider = body.provider or str(voice_cfg.get("tts_provider") or "auto")
    requested_model = body.model or str(voice_cfg.get("tts_model") or "") or None
    base_url, key, model = await _resolve_tts(
        db, user, provider=provider, model=requested_model
    )
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
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "audio/mpeg"))


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


def _audio_format(filename: str, mime: str) -> str:
    known = {
        "audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3",
        "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/ogg": "ogg",
        "audio/webm": "webm", "audio/flac": "flac", "audio/aac": "aac",
    }
    if mime.split(";", 1)[0].lower() in known:
        return known[mime.split(";", 1)[0].lower()]
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
    return suffix if suffix in {"wav", "mp3", "m4a", "ogg", "webm", "flac", "aac"} else "webm"


async def _transcribe_openrouter(
    key: str, model: str, filename: str, audio: bytes, mime: str
) -> dict:
    """OpenRouter STT usa JSON/base64 no endpoint dedicado de transcrição."""
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{s.openrouter_base_url}/audio/transcriptions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": s.openrouter_app_url,
                    "X-Title": s.openrouter_app_name,
                },
                json={
                    "model": model,
                    "input_audio": {
                        "data": base64.b64encode(audio).decode("ascii"),
                        "format": _audio_format(filename, mime),
                    },
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OpenRouter STT inacessível: {exc}") from None
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Falha no STT OpenRouter: {resp.text[:200]}")
    return resp.json()


@router.post("/stt")
async def stt(
    file: UploadFile = File(...),
    model_config_id: uuid.UUID | None = Form(default=None),
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
    voice_cfg = await _model_voice_config(db, user, model_config_id)
    provider = str(voice_cfg.get("stt_provider") or "auto")
    requested_model = str(voice_cfg.get("stt_model") or "") or None

    if provider == "openrouter":
        key = await get_secret(db, user.id, OPENROUTER_KEY)
        if not key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configure sua chave do OpenRouter primeiro")
        return await _transcribe_openrouter(
            key, requested_model or "openai/whisper-large-v3-turbo", fname, audio, mime
        )

    # 1º a conexão de voz LOCAL do usuário (stacks como speaches/faster-whisper
    # expõem /audio/transcriptions; Kokoro devolve 404 e caímos adiante)…
    prov = await voice_service.get_provider(db, user.id)
    if provider == "local" and not prov:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configure e ative o servidor de voz local primeiro")
    if provider in {"auto", "local"} and prov:
        out = await _try_transcribe(
            prov["base_url"], prov["api_key"], requested_model or s.stt_model, fname, audio, mime
        )
        if out is not None:
            return out
        if provider == "local":
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "O servidor de voz local não oferece transcrição")
    if provider not in {"auto", "api", "local"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provedor de transcrição inválido")
    # …depois o provedor global (exige a chave)
    key = await get_secret(db, user.id, VOICE_KEY)
    if not key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nenhum servidor de transcrição disponível: a conexão de Voz Local não faz "
            "STT e não há chave do provedor de voz configurada.",
        )
    out = await _try_transcribe(
        s.voice_base_url, key, requested_model or s.stt_model, fname, audio, mime
    )
    if out is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Provedor de voz global inacessível")
    return out


_OPENAI_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo", "fable", "onyx",
    "nova", "sage", "shimmer", "verse", "marin", "cedar",
]


@router.get("/catalog")
async def voice_catalog(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Catálogo especializado para os seletores por-modelo de TTS e STT."""
    key = await get_secret(db, user.id, OPENROUTER_KEY)
    tts_models: list[dict] = []
    stt_models: list[dict] = []
    if key:
        try:
            speech, transcription = await asyncio.gather(
                openrouter.list_models(key, output_modality="speech"),
                openrouter.list_models(key, output_modality="transcription"),
            )
            tts_models = [
                {"id": m.get("id"), "name": m.get("name") or m.get("id"), "provider": "OpenRouter"}
                for m in speech if m.get("id")
            ]
            stt_models = [
                {"id": m.get("id"), "name": m.get("name") or m.get("id"), "provider": "OpenRouter"}
                for m in transcription if m.get("id")
            ]
        except httpx.HTTPError:
            pass
    local_cfg = await voice_service.public_config(db, user.id)
    local_voices = await voice_service.list_user_voices(db, user.id) if local_cfg["configured"] and local_cfg["enabled"] else []
    eleven_voices = await _elevenlabs_voice_names(db, user)
    return {
        "providers": {
            "openrouter": {"configured": bool(key), "label": "OpenRouter API"},
            "api": {"configured": bool(await get_secret(db, user.id, VOICE_KEY)), "label": "API de voz"},
            "local": {"configured": bool(local_cfg["configured"] and local_cfg["enabled"]), "label": "Servidor local"},
        },
        "tts_models": tts_models,
        "stt_models": stt_models,
        "voices": [
            *[{"id": v, "name": v.title(), "provider": "OpenAI/API"} for v in _OPENAI_VOICES],
            *[{"id": v, "name": v, "provider": "Local"} for v in local_voices],
            *[{"id": v, "name": v.removeprefix(elevenlabs_service.EL_VOICE_PREFIX), "provider": "ElevenLabs"} for v in eleven_voices],
        ],
    }


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
# Credenciais da WAKE WORD (por-usuário, CIFRADAS): AccessKey Picovoice, .ppn e URL
# do Vosk. Um JSON cifrado em UserSecret (não em profile), então não vaza no dump.
# --------------------------------------------------------------------------- #
class WakeConfigIn(BaseModel):
    picovoice_key: str = Field(default="", max_length=400)
    ppn_url: str = Field(default="", max_length=600)
    vosk_model_url: str = Field(default="", max_length=600)
    # OpenWakeWord: modelo treinado do usuário (.onnx) + os 2 compartilhados
    oww_model_url: str = Field(default="", max_length=600)
    oww_melspec_url: str = Field(default="", max_length=600)
    oww_embedding_url: str = Field(default="", max_length=600)


@router.get("/wake")
async def get_wake(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    raw = await get_secret(db, user.id, WAKE_CONFIG_KEY)
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
    keys = (
        "picovoice_key", "ppn_url", "vosk_model_url",
        "oww_model_url", "oww_melspec_url", "oww_embedding_url",
    )
    return {k: str(data.get(k) or "") for k in keys}


@router.put("/wake")
async def put_wake(
    body: WakeConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    await set_secret(db, user.id, WAKE_CONFIG_KEY, json.dumps(body.model_dump()))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Modo voz (assistente): resolve o chat que o modo voz usa para um modelo.
# `filter_config.listen`: {enabled, call_name, chat_mode: fixed|new, folder,
# auto_speak, hands_free, continuous, follow_up_secs}. "fixed" reusa um chat contínuo na pasta
# padrão "Assistente" com o nome do modelo (como o WhatsApp); "new" abre um chat limpo.
# --------------------------------------------------------------------------- #
_VOICE_CHAT_TITLE = "Modo voz"
_VOICE_FOLDER = "Assistente"  # pasta padrão do chat fixo (como a do WhatsApp)


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
        # pasta padrão única "Assistente" (como o WhatsApp); o CHAT leva o nome do
        # modelo — sem subpasta e sem pasta configurável.
        model_name = (getattr(mc, "name", None) or "Modelo")[:120]
        root = await _find_or_create_folder(db, user.id, _VOICE_FOLDER, None)
        chat = await db.scalar(
            select(Chat).where(
                Chat.user_id == user.id, Chat.model_config_id == mc.id,
                Chat.folder_id == root.id, Chat.archived.is_(False),
            ).order_by(Chat.updated_at.desc()).limit(1)
        )
        if chat is None:
            chat = Chat(user_id=user.id, title=model_name, model=mc.base_model,
                        model_config_id=mc.id, params=mc.params or {}, folder_id=root.id)
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
