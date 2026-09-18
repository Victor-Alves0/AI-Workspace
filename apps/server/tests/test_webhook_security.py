"""Webhooks só podem alcançar destinos públicos e mantêm a task viva."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aiworkspace.api import keys_routes, webhooks


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/hook",
        "http://127.0.0.1/hook",
        "http://192.168.1.20/hook",
        "http://10.0.0.1/hook",
        "http://[::1]/hook",
        "https://user:pass@example.com/hook",
        "file:///tmp/hook",
    ],
)
def test_webhook_url_rejects_private_or_unsafe_destinations(url):
    assert not webhooks.is_public_webhook_url(url)
    with pytest.raises(HTTPException):
        keys_routes._clean_webhook({"url": url})


def test_webhook_url_accepts_public_http_target():
    assert webhooks.is_public_webhook_url("https://hooks.example.com/ai-workspace")
    cleaned = keys_routes._clean_webhook({"url": "https://hooks.example.com/ai-workspace"})
    assert cleaned["url"] == "https://hooks.example.com/ai-workspace"


def test_emit_uses_tracked_background_task(monkeypatch):
    scheduled = []

    def fake_spawn(coro, **kwargs):
        scheduled.append(kwargs)
        coro.close()

    monkeypatch.setattr(webhooks.bg, "spawn", fake_spawn)
    key = SimpleNamespace(
        id="key-id", name="integração", webhook={"url": "https://hooks.example.com/event"}
    )
    webhooks.emit(key, "key.created")
    assert scheduled == [{"name": "api-webhook"}]
