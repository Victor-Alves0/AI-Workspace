"""Roteamento TTS/STT por modelo e contrato dedicado do OpenRouter."""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from fastapi import HTTPException

from aiworkspace import voice_routes
from aiworkspace.secrets_service import OPENROUTER_KEY


class _Response:
    status_code = 200
    text = ""
    content = b"audio"
    headers: ClassVar[dict] = {"content-type": "audio/mpeg"}

    def json(self):
        return {"text": "olá"}


class _Client:
    last_url = ""
    last_headers: ClassVar[dict] = {}
    last_json: ClassVar[dict] = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers, json):
        _Client.last_url = url
        _Client.last_headers = headers
        _Client.last_json = json
        return _Response()


def _settings():
    return SimpleNamespace(
        openrouter_base_url="https://openrouter.test/api/v1",
        openrouter_app_url="https://ai-workspace.test",
        openrouter_app_name="AI Workspace",
        voice_base_url="https://api.openai.test/v1",
        tts_model="tts-1",
    )


def test_audio_format_normalizes_browser_recordings():
    assert voice_routes._audio_format("audio.webm", "audio/webm;codecs=opus") == "webm"
    assert voice_routes._audio_format("memo.m4a", "application/octet-stream") == "m4a"
    assert voice_routes._audio_format("sem-extensao", "application/octet-stream") == "webm"
    assert voice_routes._audio_format("audio.m4a", "audio/mp4;codecs=mp4a.40.2") == "m4a"


@pytest.mark.asyncio
async def test_openrouter_stt_uses_json_base64_contract(monkeypatch):
    monkeypatch.setattr(voice_routes, "get_settings", _settings)
    monkeypatch.setattr(voice_routes.httpx, "AsyncClient", _Client)

    out = await voice_routes._transcribe_openrouter(
        "sk-test", "openai/whisper-large-v3-turbo", "audio.webm", b"audio-bytes", "audio/webm"
    )

    assert out["text"] == "olá"
    assert _Client.last_url == "https://openrouter.test/api/v1/audio/transcriptions"
    assert _Client.last_json == {
        "model": "openai/whisper-large-v3-turbo",
        "input_audio": {
            "data": base64.b64encode(b"audio-bytes").decode("ascii"),
            "format": "webm",
        },
    }
    assert _Client.last_headers["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_openrouter_tts_reuses_user_openrouter_key(monkeypatch):
    monkeypatch.setattr(voice_routes, "get_settings", _settings)

    async def fake_secret(db, user_id, name):
        assert name == OPENROUTER_KEY
        return "sk-openrouter"

    monkeypatch.setattr(voice_routes, "get_secret", fake_secret)
    base, key, model = await voice_routes._resolve_tts(
        object(), SimpleNamespace(id="user"), provider="openrouter", model="openai/tts-model"
    )
    assert (base, key, model) == (
        "https://openrouter.test/api/v1", "sk-openrouter", "openai/tts-model"
    )


@pytest.mark.asyncio
async def test_tts_uses_latest_saved_voice_when_only_model_id_is_sent(monkeypatch):
    async def resolve(*_args, **_kwargs):
        return "https://voice.test", "key", "speech-model"

    class Database:
        async def scalar(self, _statement):
            return SimpleNamespace(filter_config={"voice": {"tts_provider": "api"}}, tts_voice="coral")

    monkeypatch.setattr(voice_routes, "_resolve_tts", resolve)
    monkeypatch.setattr(voice_routes.httpx, "AsyncClient", _Client)
    result = await voice_routes.tts(
        voice_routes.TTSIn(text="Olá", model_config_id=uuid.uuid4()),
        user=SimpleNamespace(id=uuid.uuid4()), db=Database(),
    )
    assert result.body == b"audio"
    assert _Client.last_json["voice"] == "coral"


@pytest.mark.asyncio
async def test_tts_respects_model_voice_disabled_toggle():
    class Database:
        async def scalar(self, _statement):
            return SimpleNamespace(
                filter_config={"voice": {"tts_enabled": False}},
                tts_voice="coral",
            )

    with pytest.raises(HTTPException) as exc:
        await voice_routes.tts(
            voice_routes.TTSIn(text="Olá", model_config_id=uuid.uuid4()),
            user=SimpleNamespace(id=uuid.uuid4()),
            db=Database(),
        )

    assert exc.value.status_code == 400
    assert "desativada" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_stt_respects_model_voice_disabled_toggle_before_reading_audio():
    class Database:
        async def scalar(self, _statement):
            return SimpleNamespace(
                filter_config={"voice": {"stt_enabled": False}},
                tts_voice=None,
            )

    class UnreadFile:
        async def read(self, _limit):
            raise AssertionError("disabled STT must not consume the upload")

    with pytest.raises(HTTPException) as exc:
        await voice_routes.stt(
            file=UnreadFile(),
            model_config_id=uuid.uuid4(),
            user=SimpleNamespace(id=uuid.uuid4()),
            db=Database(),
        )

    assert exc.value.status_code == 400
    assert "desativada" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_audio_catalog_keeps_stt_when_tts_catalog_is_unavailable(monkeypatch):
    async def secret(*_args):
        return "key"

    async def catalog(_key, *, output_modality):
        if output_modality == "speech":
            raise httpx.ConnectError("speech catalog unavailable")
        return [{"id": "speech-to-text", "name": "Transcription"}]

    async def local(*_args):
        return {"configured": False, "enabled": False}

    async def eleven(*_args):
        return []

    monkeypatch.setattr(voice_routes, "get_secret", secret)
    monkeypatch.setattr(voice_routes.openrouter, "list_models", catalog)
    monkeypatch.setattr(voice_routes.voice_service, "public_config", local)
    monkeypatch.setattr(voice_routes, "_elevenlabs_voice_names", eleven)
    result = await voice_routes.voice_catalog(user=SimpleNamespace(id="user"), db=object())
    assert result["tts_models"] == []
    assert result["stt_models"][0]["id"] == "speech-to-text"
