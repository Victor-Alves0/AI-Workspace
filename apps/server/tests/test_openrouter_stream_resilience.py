"""Regressões puras do payload/stream OpenRouter e compatíveis."""

from __future__ import annotations

import pytest

from aiworkspace.providers import openrouter


def test_cache_marker_targets_text_not_trailing_image():
    content = [
        {"type": "text", "text": "descreva"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]
    marked = openrouter._mark_content_cache(content)
    assert marked is not content
    assert marked[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in marked[1]
    assert "cache_control" not in content[0]  # não muta o payload do chamador


class _StreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        yield 'data: {"error":{"message":"quota exhausted"}}'
        yield "data: [DONE]"


class _StreamClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _StreamResponse()


@pytest.mark.asyncio
async def test_http_200_sse_error_is_raised(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _StreamClient)
    with pytest.raises(RuntimeError, match="quota exhausted"):
        async for _ in openrouter.stream_chat(
            "key", "provider/model", [{"role": "user", "content": "oi"}]
        ):
            pass


class _DoneStreamResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        yield "data: [DONE]"


class _CapturingStreamClient:
    last_json: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        self.__class__.last_json = kwargs["json"]
        return _DoneStreamResponse()


@pytest.mark.asyncio
async def test_openrouter_preserves_provider_routing_preferences(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _CapturingStreamClient)
    async for _ in openrouter.stream_chat(
        "key",
        "provider/model",
        [{"role": "user", "content": "oi"}],
        params={"provider": {"sort": "latency", "allow_fallbacks": False}},
    ):
        pass
    assert _CapturingStreamClient.last_json["provider"] == {
        "sort": "latency", "allow_fallbacks": False,
    }


@pytest.mark.asyncio
async def test_compat_provider_does_not_receive_openrouter_routing(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _CapturingStreamClient)
    async for _ in openrouter.stream_chat(
        "key",
        "local/model",
        [{"role": "user", "content": "oi"}],
        params={"provider": {"sort": "latency"}},
        base_url="http://ollama:11434/v1",
    ):
        pass
    assert "provider" not in _CapturingStreamClient.last_json
