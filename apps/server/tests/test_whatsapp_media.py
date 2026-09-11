"""Envio de anexos nas duas implementações de WhatsApp."""
from __future__ import annotations

from typing import ClassVar

from aiworkspace.integrations import telegram_api, whatsapp_service
from aiworkspace.integrations import whatsapp_evolution as evolution
from aiworkspace.integrations import whatsapp_official as official


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data

    status_code = 200


class _Client:
    calls: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if str(url).endswith("/media"):
            return _Response({"id": "media-123"})
        return _Response({"messages": [{"id": "message-123"}]})


async def test_evolution_uses_document_media_type(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(evolution.httpx, "AsyncClient", _Client)
    await evolution.send_media("inst", "5511999", b"pdf", "application/pdf", "manual.pdf")
    call = _Client.calls[0]
    assert call["data"]["mediatype"] == "document"
    assert call["data"]["fileName"] == "manual.pdf"
    assert call["files"]["file"] == ("manual.pdf", b"pdf", "application/pdf")
    assert "media" not in call["data"]


async def test_evolution_uses_video_media_type(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(evolution.httpx, "AsyncClient", _Client)
    await evolution.send_media("inst", "5511999", b"video", "video/mp4", "demo.mp4")
    assert _Client.calls[0]["data"]["mediatype"] == "video"
    assert _Client.calls[0]["files"]["file"] == ("demo.mp4", b"video", "video/mp4")


async def test_official_uploads_then_sends_document(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(official.httpx, "AsyncClient", _Client)
    await official.send_media(
        "phone-id", "secret", "5511999@s.whatsapp.net", b"pdf", "application/pdf",
        "manual.pdf", "Leia isto",
    )

    assert len(_Client.calls) == 2
    upload, send = _Client.calls
    assert upload["url"].endswith("/phone-id/media")
    assert upload["data"] == {"messaging_product": "whatsapp", "type": "application/pdf"}
    assert upload["files"]["file"] == ("manual.pdf", b"pdf", "application/pdf")
    assert send["json"]["to"] == "5511999"
    assert send["json"]["type"] == "document"
    assert send["json"]["document"] == {
        "id": "media-123", "caption": "Leia isto", "filename": "manual.pdf",
    }


async def test_official_sends_video_with_uploaded_id(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(official.httpx, "AsyncClient", _Client)
    await official.send_media("phone-id", "secret", "5511", b"v", "video/mp4", "v.mp4")
    payload = _Client.calls[1]["json"]
    assert payload["type"] == "video"
    assert payload["video"] == {"id": "media-123"}


async def test_service_dispatches_media_to_official_provider(monkeypatch):
    seen = {}

    async def fake_send_media(*args):
        seen["args"] = args

    monkeypatch.setattr(official, "send_media", fake_send_media)
    conn = type("Connection", (), {
        "provider": "official", "phone_number_id": "phone-id", "access_token": "secret",
    })()
    item = {
        "data": b"img", "mime": "image/png", "filename": "foto.png", "caption": "Oi",
    }
    await whatsapp_service._send_media(conn, "5511", item)
    assert seen["args"] == (
        "phone-id", "secret", "5511", b"img", "image/png", "foto.png", "Oi",
    )


async def test_telegram_uses_document_endpoint_and_mime(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(telegram_api.httpx, "AsyncClient", _Client)
    await telegram_api.send_media(
        "bot", "chat", b"pdf", "manual.pdf", "application/pdf", "Leia",
    )
    call = _Client.calls[0]
    assert call["url"].endswith("/sendDocument")
    assert call["files"]["document"] == ("manual.pdf", b"pdf", "application/pdf")
