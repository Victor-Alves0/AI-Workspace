"""Análise de gargalos contra o Postgres real: operações agregadas (total/próprio),
detalhe de uma etapa (atributos numéricos, agrupamento, mais lentas), endpoint por
dentro (etapas por chamada, histograma, filhos) e a cadeia no detalhe do trace."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


@pytest.fixture
def cliente(banco, engine, monkeypatch):
    from aiworkspace import audit_service, db as dbmod, main
    from aiworkspace.tracing import sink

    migrar(engine, "head")
    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    fabrica = async_sessionmaker(eng, expire_on_commit=False)

    async def _db():
        async with fabrica() as s:
            yield s

    async def _sem_auditoria(*_a, **_kw):
        return None

    monkeypatch.setattr(audit_service, "record", _sem_auditoria)
    monkeypatch.setattr(sink, "SessionLocal", fabrica)
    app = main.create_app()
    app.dependency_overrides[dbmod.get_db] = _db
    c = TestClient(app)
    assert c.post("/auth/setup", json={"name": "Dono", "email": "dono@casa.local",
                                       "password": "Senha-forte-1"}).status_code == 200
    return c, sink


def _semear(sink):
    """3 POSTs de mensagem; cada um com 2 etapas de setup e uma geração FILHA com 2
    chamadas ao modelo (uma lenta) e uma ferramenta."""
    from aiworkspace import tracing
    from aiworkspace.tracing import context

    traces = []
    for i in range(3):
        req = tracing.new_trace("POST /chats/{chat_id}/messages", kind="chat",
                                method="POST", path="/chats/{chat_id}/messages")
        tok = context._current_trace.set(req)
        try:
            with tracing.span("setup:tools") as sp:
                sp._t0 -= 0.2           # 200ms
            with tracing.span("setup:skills") as sp:
                sp._t0 -= 0.01
            gen = tracing.new_trace("chat:generation", kind="chat")
        finally:
            context._current_trace.reset(tok)
        req.close()
        req.duration_ms = 300.0
        tok = context._current_trace.set(gen)
        try:
            with tracing.span("llm:deepseek", kind="llm") as llm:
                llm._t0 -= 2.0
                llm.set(ttfb_ms=1500.0, tokens_per_s=40.0)
                with tracing.span("http:openrouter.ai", kind="http", path="/api/v1/chat/completions") as h:
                    h._t0 -= 1.5
            with tracing.span("tool:web.search.query", kind="tool", inner="web.search.query") as t:
                t._t0 -= 0.5
                if i == 2:
                    t.status, t.error = "error", "timeout"
        finally:
            context._current_trace.reset(tok)
        gen.close()
        traces += [req, gen]
    asyncio.run(sink._write_batch(traces))
    return traces


def test_analise_de_gargalos(cliente):
    c, sink = cliente
    traces = _semear(sink)

    ops = {o["name"]: o for o in c.get("/observability/operations?hours=1").json()["operations"]}
    llm = ops["llm:deepseek"]
    assert llm["count"] == 3 and llm["p50_ms"] >= 2000
    # tempo próprio do llm desconta o http filho (~1,5s)
    assert 400 <= llm["avg_self_ms"] <= 700
    assert ops["tool:web.search.query"]["errors"] == 1
    assert list(ops)[0] == "llm:deepseek"                 # onde mais se gasta tempo

    det = c.get("/observability/operations/detail", params={"name": "llm:deepseek", "hours": 1}).json()
    assert det["count"] == 3
    assert det["attrs"]["ttfb_ms"]["avg"] == 1500.0 and det["attrs"]["tokens_per_s"]["p50"] == 40.0
    assert det["slowest"][0]["trace_id"]

    tool = c.get("/observability/operations/detail", params={"name": "tool:web.search.query", "hours": 1}).json()
    assert tool["errors"] == [{"error": "timeout", "count": 1}]
    assert tool["groups"][0]["key"] == "web.search.query"

    rota = c.get("/observability/route", params={"path": "/chats/{chat_id}/messages", "method": "POST",
                                                  "hours": 1}).json()
    assert rota["count"] == 3
    etapas = {b["name"]: b for b in rota["breakdown"]}
    assert 190 <= etapas["setup:tools"]["per_call_ms"] <= 260
    assert rota["children"][0]["name"] == "chat:generation" and rota["children"][0]["count"] == 3
    assert sum(h["count"] for h in rota["histogram"]) == 3

    req, gen = traces[0], traces[1]
    d = c.get(f"/observability/traces/{uuid.UUID(req.id)}").json()
    assert [k["name"] for k in d["children"]] == ["chat:generation"]
    d2 = c.get(f"/observability/traces/{uuid.UUID(gen.id)}").json()
    assert d2["parent"]["name"] == "POST /chats/{chat_id}/messages"

    rt = c.get("/observability/runtime").json()
    assert "loop_lag" in rt and "sink" in rt
