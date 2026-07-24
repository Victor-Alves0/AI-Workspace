"""Cache TTL do catálogo de modelos do OpenRouter.

O seletor de modelo buscava o catálogo (~300ms de rede) a CADA abertura; o cache
serve as aberturas seguintes de graça. Puro/hermético: mocka o httpx e conta os
fetches reais.
"""

from __future__ import annotations

import pytest

from aiworkspace.providers import openrouter


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._data}


class _FakeClient:
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        _FakeClient.calls += 1
        return _FakeResp([{"id": "m1"}, {"id": "m2"}])


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    openrouter.invalidate_catalog()
    _FakeClient.calls = 0
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _FakeClient)
    yield
    openrouter.invalidate_catalog()


@pytest.mark.asyncio
async def test_catalog_is_fetched_once_then_cached():
    a = await openrouter.list_models("key")
    b = await openrouter.list_models("key")
    c = await openrouter.list_models("key")
    assert a == b == c
    assert len(a) == 2
    assert _FakeClient.calls == 1  # só o primeiro bateu na rede


@pytest.mark.asyncio
async def test_force_refetches():
    await openrouter.list_models("key")
    await openrouter.list_models("key", force=True)
    assert _FakeClient.calls == 2


@pytest.mark.asyncio
async def test_invalidate_forces_next_fetch():
    await openrouter.list_models("key")
    openrouter.invalidate_catalog()
    await openrouter.list_models("key")
    assert _FakeClient.calls == 2


@pytest.mark.asyncio
async def test_expiry_refetches(monkeypatch):
    await openrouter.list_models("key")
    monkeypatch.setattr(openrouter, "_CATALOG_TTL", -1)  # tudo já expirou
    await openrouter.list_models("key")
    assert _FakeClient.calls == 2
