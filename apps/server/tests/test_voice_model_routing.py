"""Roteamento TTS/STT por modelo e contrato dedicado do OpenRouter."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import ClassVar

import pytest

from aiworkspace import voice_routes
from aiworkspace.secrets_service import OPENROUTER_KEY


class _Response:
    status_code = 200
    text = ""

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
