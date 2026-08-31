"""Núcleo do rastreamento: contexto, aninhamento, amostragem e normalização.

Puro/hermético — não toca banco nem sink real. Cobre as decisões que, se saírem
erradas, corrompem TODO o rastro: propagação de contexto, aninhamento de spans,
atribuição de banco, o guard de teto e a regra de amostragem.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace import tracing
from aiworkspace.tracing import context as ctx
from aiworkspace.tracing import instrument
from aiworkspace.tracing import sink


@pytest.fixture(autouse=True)
def _no_sink(monkeypatch):
    """Neutraliza o sink: os testes inspecionam o objeto Trace em memória, não o
    banco. Sem isto, fechar um trace tentaria enfileirar."""
    monkeypatch.setattr(sink, "submit", lambda tr: None)
    yield


# --------------------------------------------------------------------------- #
# Contexto e aninhamento
# --------------------------------------------------------------------------- #

def test_trace_captures_nested_spans():
    with tracing.start_trace("t", kind="http") as tr:
        with tracing.span("a", kind="internal"):
            with tracing.span("b", kind="db"):
                pass
        with tracing.span("c", kind="llm"):
            pass
    names = [s.name for s in tr.spans]
    assert names == ["b", "a", "c"]  # fecham de dentro para fora
    by_name = {s.name: s for s in tr.spans}
    assert by_name["b"].parent_id == by_name["a"].id
    assert by_name["a"].parent_id is None
    assert by_name["c"].parent_id is None


def test_current_trace_and_span_track_context():
    assert tracing.current_trace() is None
    with tracing.start_trace("t", kind="http") as tr:
        assert tracing.current_trace() is tr
        assert tracing.current_span() is None
        with tracing.span("s") as sp:
            assert tracing.current_span() is sp
        assert tracing.current_span() is None
    assert tracing.current_trace() is None


def test_nested_trace_is_reused_not_restarted():
    """Instrumentar um caminho que já roda dentro de um trace não deve criar outro
    — o start_trace aninhado reutiliza o corrente."""
    with tracing.start_trace("outer", kind="http") as outer:
        with tracing.start_trace("inner", kind="chat") as inner:
            assert inner is outer
            with tracing.span("s"):
                pass
    assert len(outer.spans) == 1


def test_detached_trace_replaces_parent_context_and_flushes_once(monkeypatch):
    """Trabalho em background não pode anexar spans ao trace HTTP já encerrado."""
    submitted = []
    monkeypatch.setattr(sink, "submit", submitted.append)
    detached = tracing.new_trace("chat:generation", kind="chat", chat_id="c1")
    with tracing.start_trace("POST /chats", kind="http") as request_trace:
        with tracing.activate_trace(detached) as active:
            assert active is detached
            assert tracing.current_trace() is detached
            assert tracing.get_open_trace(detached.id) is detached
            with tracing.span("llm:model", kind="llm"):
                pass
        assert tracing.current_trace() is request_trace
        assert tracing.get_open_trace(detached.id) is None
    assert submitted == [detached, request_trace]
    assert detached.spans[0].trace_id == detached.id
    assert detached.attrs["chat_id"] == "c1"


def test_span_without_trace_is_noop():
    """span() fora de um trace mede mas não persiste — não pode quebrar."""
    assert tracing.current_trace() is None
    with tracing.span("orfao", kind="db") as sp:
        pass
    assert sp.trace_id == ""


# --------------------------------------------------------------------------- #
# Erros
# --------------------------------------------------------------------------- #

def test_error_in_span_marks_span_and_trace():
    with pytest.raises(ValueError):
        with tracing.start_trace("t", kind="http") as tr:
            with tracing.span("s") as sp:
                raise ValueError("boom")
    assert sp.status == "error"
    assert "boom" in sp.error
    assert tr.status == "error"
    assert "boom" in tr.error


def test_record_error_does_not_raise_without_context():
    tracing.record_error(ValueError("x"))  # sem trace ativo: silencioso


# --------------------------------------------------------------------------- #
# Atribuição de banco
# --------------------------------------------------------------------------- #

def test_db_stats_attach_to_current_span():
    with tracing.start_trace("t", kind="http") as tr:
        with tracing.span("s") as sp:
            sp.add_db(write=False, ms=3.0)
            sp.add_db(write=True, ms=1.5)
    assert sp.db_reads == 1
    assert sp.db_writes == 1
    assert round(sp.db_ms, 1) == 4.5


def test_db_stats_without_span_go_to_trace_root():
    with tracing.start_trace("t", kind="http") as tr:
        tr.add_root_db(write=False, ms=2.0)
        tr.add_root_db(write=True, ms=1.0)
    assert tr.root_db_reads == 1
    assert tr.root_db_writes == 1
    assert round(tr.root_db_ms, 1) == 3.0


# --------------------------------------------------------------------------- #
# Guard de teto e clipe de atributo
# --------------------------------------------------------------------------- #

def test_span_cap_drops_excess(monkeypatch):
    monkeypatch.setattr(ctx, "_MAX_SPANS_PER_TRACE", 3)
    with tracing.start_trace("t", kind="http") as tr:
        for i in range(10):
            with tracing.span(f"s{i}"):
                pass
    assert len(tr.spans) == 3
    assert tr.dropped_spans == 7


def test_long_attr_is_clipped():
    with tracing.start_trace("t", kind="http") as tr:
        with tracing.span("s") as sp:
            sp.set(big="x" * 9000)
    assert len(sp.attrs["big"]) <= ctx._MAX_ATTR_LEN + 1


# --------------------------------------------------------------------------- #
# Amostragem (sink._keep)
# --------------------------------------------------------------------------- #

def _trace(status="ok", ms=10.0):
    return SimpleNamespace(status=status, duration_ms=ms)


def test_sampling_keeps_everything_at_rate_1(monkeypatch):
    monkeypatch.setattr(sink, "get_settings",
                        lambda: SimpleNamespace(obs_sample_rate=1.0, obs_slow_ms=1500))
    assert sink._keep(_trace()) is True


def test_sampling_always_keeps_errors_and_slow(monkeypatch):
    monkeypatch.setattr(sink, "get_settings",
                        lambda: SimpleNamespace(obs_sample_rate=0.0, obs_slow_ms=1500))
    assert sink._keep(_trace(status="error")) is True     # erro sempre
    assert sink._keep(_trace(ms=5000)) is True            # lento sempre
    assert sink._keep(_trace(status="ok", ms=10)) is False  # normal, taxa 0 -> descarta


# --------------------------------------------------------------------------- #
# Normalização de SQL (nunca vaza valores)
# --------------------------------------------------------------------------- #

def test_normalize_sql_strips_literals():
    out = instrument.normalize_sql(
        "SELECT * FROM users WHERE email = 'a@b.com' AND age = 42"
    )
    assert "a@b.com" not in out
    assert "42" not in out
    assert "?" in out
    assert "FROM users" in out


def test_is_write_classification():
    assert instrument._is_write("INSERT INTO x VALUES (1)") is True
    assert instrument._is_write("  update x set a=1") is True
    assert instrument._is_write("DELETE FROM x") is True
    assert instrument._is_write("SELECT 1") is False
    assert instrument._is_write("  with cte as (select 1) select * from cte") is False
