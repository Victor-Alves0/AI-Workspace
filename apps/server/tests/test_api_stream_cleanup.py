"""Encerramento dos streams da API pública — sobretudo quando o CLIENTE CAI.

Regressão de um bug real: o `finally` dos geradores de streaming COMEÇAVA com
`yield "data: [DONE]"`. Quando o cliente desconecta, o Starlette fecha o gerador
(`aclose()`), o que lança GeneratorExit no ponto suspenso; um `yield` dentro do
`finally` nessa hora vira `RuntimeError: async generator ignored GeneratorExit` e
ABORTA o resto do bloco. Consequências: a vaga de concorrência nunca era devolvida
(a chave travava em 429 até reiniciar) e `runner.record` não rodava — a chamada não
entrava em `api_requests` e portanto não contava para RPD/mensal/tokens/orçamento,
ou seja, desconectar no meio do stream dava uso de modelo fora das cotas.

Hermético: sem banco e sem rede (SessionLocal, run_* e record são fakes).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace.api import limits, v1_routes


class _FakeSession:
    """Substitui `SessionLocal()` — o `finally` do gerador abre uma sessão."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _ctx():
    key = SimpleNamespace(id="k1", limits={"concurrency": 1})
    return SimpleNamespace(key=key, db=None)


@pytest.fixture(autouse=True)
def _clean():
    limits.reset()
    yield
    limits.reset()


@pytest.fixture
def patched(monkeypatch):
    """Neutraliza banco/rede e registra as chamadas de `record`."""
    recorded: list[dict] = []

    async def fake_record(db, ctx, **kw):
        recorded.append(kw)

    monkeypatch.setattr(v1_routes, "SessionLocal", _FakeSession)
    monkeypatch.setattr(v1_routes.runner, "record", fake_record)
    monkeypatch.setattr(v1_routes.runner, "usage_record_for", lambda rm, usage: None)
    monkeypatch.setattr(v1_routes.webhooks, "emit", lambda *a, **k: None)
    return recorded


# --------------------------------------------------------------------------- #
# passthrough
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_passthrough_stream_cleans_up_when_client_disconnects(patched, monkeypatch):
    async def endless(rm, messages, body):
        for i in range(1000):
            yield {"choices": [{"delta": {"content": str(i)}}]}

    monkeypatch.setattr(v1_routes.runner, "run_passthrough", endless)
    ctx = _ctx()
    assert limits.acquire_slot(ctx.key) is True
    assert limits.inflight(ctx.key) == 1

    gen = v1_routes._stream_passthrough(ctx, SimpleNamespace(public_id="m"), [], {}, 0.0)
    assert await gen.__anext__()      # stream começou
    await gen.aclose()                # <- o cliente caiu no meio

    assert limits.inflight(ctx.key) == 0, "a vaga de concorrência ficou presa"
    assert patched, "a chamada não foi registrada (escaparia das cotas)"


@pytest.mark.asyncio
async def test_passthrough_stream_cleans_up_on_normal_end(patched, monkeypatch):
    async def two(rm, messages, body):
        yield {"choices": [{"delta": {"content": "a"}}]}
        yield {"choices": [{"delta": {"content": "b"}}], "usage": {"total_tokens": 3}}

    monkeypatch.setattr(v1_routes.runner, "run_passthrough", two)
    ctx = _ctx()
    limits.acquire_slot(ctx.key)

    out = [c async for c in v1_routes._stream_passthrough(
        ctx, SimpleNamespace(public_id="m"), [], {}, 0.0)]

    assert out[-1] == "data: [DONE]\n\n", "a sentinela do protocolo sumiu"
    assert limits.inflight(ctx.key) == 0
    assert patched and patched[0]["status"] == 200


@pytest.mark.asyncio
async def test_passthrough_stream_cleans_up_on_upstream_error(patched, monkeypatch):
    async def boom(rm, messages, body):
        yield {"choices": [{"delta": {"content": "a"}}]}
        raise RuntimeError("provedor caiu")

    monkeypatch.setattr(v1_routes.runner, "run_passthrough", boom)
    ctx = _ctx()
    limits.acquire_slot(ctx.key)

    out = [c async for c in v1_routes._stream_passthrough(
        ctx, SimpleNamespace(public_id="m"), [], {}, 0.0)]

    assert any("provedor caiu" in c for c in out)
    assert limits.inflight(ctx.key) == 0
    assert patched[0]["status"] == 502


# --------------------------------------------------------------------------- #
# plataforma
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_platform_stream_cleans_up_when_client_disconnects(patched, monkeypatch):
    async def endless(ctx, rm, parsed, body):
        for i in range(1000):
            yield {"type": "token", "text": str(i)}

    monkeypatch.setattr(v1_routes.runner, "run_platform_turn", endless)
    ctx = _ctx()
    assert limits.acquire_slot(ctx.key) is True

    gen = v1_routes._stream_platform(ctx, SimpleNamespace(public_id="m"), [], {}, "cid", 0.0)
    await gen.__anext__()             # chunk inicial (role=assistant)
    await gen.__anext__()             # 1º token
    await gen.aclose()                # <- o cliente caiu no meio

    assert limits.inflight(ctx.key) == 0, "a vaga de concorrência ficou presa"
    assert patched, "a chamada não foi registrada (escaparia das cotas)"


@pytest.mark.asyncio
async def test_platform_stream_emits_done_and_releases_on_normal_end(patched, monkeypatch):
    async def turn(ctx, rm, parsed, body):
        yield {"type": "token", "text": "oi"}
        yield {"type": "done", "content": "oi", "usage": {"total_tokens": 5}}

    monkeypatch.setattr(v1_routes.runner, "run_platform_turn", turn)
    ctx = _ctx()
    limits.acquire_slot(ctx.key)

    out = [c async for c in v1_routes._stream_platform(
        ctx, SimpleNamespace(public_id="m"), [], {}, "cid", 0.0)]

    assert out[-1] == "data: [DONE]\n\n"
    assert limits.inflight(ctx.key) == 0
    assert patched[0]["status"] == 200


@pytest.mark.asyncio
async def test_record_failure_still_releases_the_slot(patched, monkeypatch):
    """A contabilidade não pode impedir a devolução da vaga."""
    async def one(rm, messages, body):
        yield {"choices": [{"delta": {"content": "a"}}]}

    async def broken_record(db, ctx, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(v1_routes.runner, "run_passthrough", one)
    monkeypatch.setattr(v1_routes.runner, "record", broken_record)
    ctx = _ctx()
    limits.acquire_slot(ctx.key)

    out = [c async for c in v1_routes._stream_passthrough(
        ctx, SimpleNamespace(public_id="m"), [], {}, 0.0)]

    assert out[-1] == "data: [DONE]\n\n"
    assert limits.inflight(ctx.key) == 0, "falha ao registrar prendeu a vaga"


# --------------------------------------------------------------------------- #
# posse da vaga (double-release)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_passthrough_sync_does_not_release_the_slot(monkeypatch):
    """A posse é da ROTA nos caminhos síncronos. Quando `_passthrough_sync`
    também liberava, um erro no `record` fazia a rota liberar DE NOVO — e o
    segundo decremento derrubava a vaga de outra requisição em voo."""
    async def one(rm, messages, body):
        yield {"choices": [{"delta": {"content": "a"}}], "usage": {}}

    async def fake_record(db, ctx, **kw):
        return None

    monkeypatch.setattr(v1_routes.runner, "run_passthrough", one)
    monkeypatch.setattr(v1_routes.runner, "record", fake_record)

    ctx = _ctx()
    limits.acquire_slot(ctx.key)
    await v1_routes._passthrough_sync(ctx, SimpleNamespace(public_id="m"), [], {}, "cid", 0.0)
    assert limits.inflight(ctx.key) == 1, "liberou a vaga que pertence à rota"


def test_release_slot_never_goes_negative():
    key = SimpleNamespace(id="k9", limits={"concurrency": 2})
    limits.acquire_slot(key)
    limits.release_slot(key)
    limits.release_slot(key)          # release a mais não pode virar crédito
    assert limits.inflight(key) == 0
    assert limits.acquire_slot(key) is True
    assert limits.acquire_slot(key) is True
    assert limits.acquire_slot(key) is False   # teto respeitado


# --------------------------------------------------------------------------- #
# throttle de last_used_at
# --------------------------------------------------------------------------- #
def test_should_touch_throttles_per_key():
    k1 = SimpleNamespace(id="a")
    k2 = SimpleNamespace(id="b")
    assert limits.should_touch(k1) is True
    assert limits.should_touch(k1) is False, "gravaria em toda requisição"
    assert limits.should_touch(k2) is True, "o throttle é POR chave"
    assert limits.should_touch(k1, every=0) is True, "janela vencida volta a gravar"
