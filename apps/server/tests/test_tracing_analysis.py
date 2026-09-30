"""Coleta nova da Observabilidade: cadeia entre traces (pai/filho), tarefas em 2º plano
com trace próprio, toda requisição httpx vira span, marcos de tempo, etapas medidas
por decorador, turno sem trace (canal/API em streaming) ganha um, e o medidor de
travadas do event loop."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from aiworkspace import bg, tracing
from aiworkspace.tracing import context, instrument, runtime, sink


@pytest.fixture
def enviados(monkeypatch):
    lista = []
    monkeypatch.setattr(sink, "submit", lambda tr: lista.append(tr))
    return lista


def test_trace_novo_guarda_o_pai_e_o_pai_lista_o_filho(enviados):
    with tracing.start_trace("POST /chats/{id}/messages", kind="chat", user_id="u1") as pai:
        with tracing.span("setup:tools"):
            filho = tracing.new_trace("chat:generation", kind="chat")
    assert filho.attrs["parent_trace"] == pai.id
    assert filho.attrs["parent_span_name"] == "setup:tools"
    assert filho.user_id == "u1"                      # herda o dono
    assert pai.attrs["child_traces"] == [filho.id]


def test_start_trace_com_trace_fechado_abre_um_novo_ligado(enviados):
    with tracing.start_trace("GET /v1/chat", kind="api") as req:
        pass
    assert req.closed
    tok = context._current_trace.set(req)            # corpo de streaming depois dos headers
    try:
        with tracing.start_trace("turn", kind="api") as corpo:
            assert corpo is not req
            assert corpo.attrs["parent_trace"] == req.id
    finally:
        context._current_trace.reset(tok)


def test_tarefa_em_segundo_plano_ganha_trace_proprio(enviados):
    async def trabalho():
        with tracing.span("agent:Pesquisador"):
            await asyncio.sleep(0)
        return tracing.current_trace()

    async def go():
        with tracing.start_trace("chat:generation", kind="chat") as pai:
            task = bg.spawn(trabalho(), name="x", trace="agentes-bg:Wave 1")
        dentro = await task
        return pai, dentro

    pai, dentro = asyncio.run(go())
    assert dentro is not pai
    assert dentro.name == "agentes-bg:Wave 1"
    assert dentro.attrs["parent_trace"] == pai.id
    assert [s.name for s in dentro.spans] == ["agent:Pesquisador"]
    assert dentro in enviados


def test_tarefa_sem_trace_nao_cria_trace(enviados):
    async def go():
        return await bg.spawn(asyncio.sleep(0, result=tracing.current_trace()))

    assert asyncio.run(go()) is None


def test_bg_sem_trabalho_medido_e_descartado(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(sink, "get_settings", lambda: SimpleNamespace(obs_sample_rate=1.0, obs_slow_ms=1500))
    vazio = tracing.new_trace("bg:_expire", kind="worker", bg=True)
    vazio.duration_ms = 60000
    assert sink._keep(vazio) is False
    with_span = tracing.new_trace("bg:x", kind="worker", bg=True)
    with_span.spans.append(context.Span(id="s", trace_id=with_span.id, parent_id=None, name="a",
                                        kind="internal", started_wall=0, _t0=0))
    assert sink._keep(with_span) is True


def test_httpx_vira_span_com_host_caminho_e_status(enviados):
    instrument.install_httpx()

    def responde(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429 if "lim" in req.url.path else 200, json={"ok": True})

    async def go():
        transport = httpx.MockTransport(responde)
        async with httpx.AsyncClient(transport=transport) as c:
            with tracing.start_trace("t", kind="http") as tr:
                await c.get("https://api.exemplo.com/v1/users/0123456789abcdef0123/items?key=SEGREDO")
                await c.get("https://api.exemplo.com/lim")
        return tr

    tr = asyncio.run(go())
    a, b = tr.spans
    assert a.name == "http:api.exemplo.com" and a.kind == "http"
    assert a.attrs["path"] == "/v1/users/:id/items"            # sem query (chave) e com :id
    assert a.attrs["status_code"] == 200 and a.status == "ok"
    assert b.status == "error" and b.error == "HTTP 429"


def test_httpx_sem_trace_nao_mede():
    instrument.install_httpx()

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as c:
            return (await c.get("https://x.com/")).status_code

    assert asyncio.run(go()) == 200


def test_marco_so_grava_a_primeira_vez():
    sp = context.Span(id="s", trace_id="t", parent_id=None, name="llm:x", kind="llm",
                      started_wall=0, _t0=context._now())
    sp.mark("ttfb_ms")
    primeiro = sp.attrs["ttfb_ms"]
    sp.mark("ttfb_ms")
    assert sp.attrs["ttfb_ms"] == primeiro and primeiro >= 0


def test_decorador_traced_mede_a_etapa(enviados):
    @tracing.traced("setup:skills")
    async def carrega():
        return 7

    async def go():
        with tracing.start_trace("t", kind="http") as tr:
            assert await carrega() == 7
        return tr

    tr = asyncio.run(go())
    assert [s.name for s in tr.spans] == ["setup:skills"]


def test_turno_sem_trace_ganha_um(monkeypatch, enviados):
    from aiworkspace.chat import orchestrator

    async def fake_run_turn(**kw):
        with tracing.span("llm:fake", kind="llm"):
            pass
        yield {"type": "done", "content": "ok"}

    monkeypatch.setattr(orchestrator, "run_turn", fake_run_turn)

    async def go():
        sess = orchestrator.TurnSession(user_id="u9", chat_id="c1", background=True)
        return [ev async for ev in orchestrator.run_turn_guarded(session=sess, model="m")]

    evs = asyncio.run(go())
    assert evs[-1]["type"] == "done"
    tr = enviados[-1]
    assert tr.name == "turn:canal" and tr.kind == "channel" and tr.user_id == "u9"
    assert [s.name for s in tr.spans] == ["llm:fake"]


def test_travada_do_loop_vira_trace_com_suspeitos(enviados):
    aberto = tracing.new_trace("chat:generation", kind="chat")
    context._open_traces[aberto.id] = aberto
    try:
        runtime._record_stall(1000.0, 1800.0)
    finally:
        context._open_traces.pop(aberto.id, None)
    tr = enviados[-1]
    assert tr.name == "runtime:loop-stall" and tr.status == "error"
    assert tr.attrs["suspects"][0]["name"] == "chat:generation"
    snap = runtime.snapshot()
    assert snap["stalls"][0]["lag_ms"] == 1800.0
    assert {"loop_lag", "open_traces", "db_pool", "bg_tasks"} <= set(snap)


def test_nome_de_tarefa_sem_ids():
    assert bg._sem_ids("subagent-wake-17a99f62-8d3e-4770-b08a-41ce24f699d9") == "subagent-wake"
    assert bg._sem_ids("subagent-ckpt-60dd83f828") == "subagent-ckpt"


def test_contexto_ssl_e_compartilhado_entre_clientes():
    """Cada AsyncClient() carregava as CAs de novo (~400ms síncronos no loop)."""
    from aiworkspace import net

    net.install_ssl_cache()
    a = httpx.AsyncClient()
    b = httpx.AsyncClient()
    ctx_a = a._transport._pool._ssl_context
    ctx_b = b._transport._pool._ssl_context
    assert ctx_a is ctx_b
    # verify=False segue sem cache (contexto próprio)
    c = httpx.AsyncClient(verify=False)
    assert c._transport._pool._ssl_context is not ctx_a
