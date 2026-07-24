"""Conversão trace→linhas do sink (o que de fato vai para o Postgres).

Puro/hermético: monta um Trace em memória e verifica as linhas que `_rows`
produz — os totais somam spans + trabalho de banco do handler, os offsets são
não-negativos, e o llm_ms agrega só spans kind=llm.
"""

from __future__ import annotations

import time

from aiworkspace.tracing import sink
from aiworkspace.tracing.context import Span, Trace


def _span(name, kind, dur_ms, *, t0_offset=0.0, reads=0, writes=0, db_ms=0.0):
    now = time.time()
    sp = Span(id=name, trace_id="t", parent_id=None, name=name, kind=kind,
              started_wall=now + t0_offset, _t0=0.0)
    sp.duration_ms = dur_ms
    sp.db_reads, sp.db_writes, sp.db_ms = reads, writes, db_ms
    return sp


def _trace():
    now = time.time()
    return Trace(id="t1", name="POST /x", kind="http", started_wall=now, _t0=0.0,
                 method="POST", path="/x", status_code=200, duration_ms=500.0)


def test_rows_aggregate_span_and_root_db():
    tr = _trace()
    tr.spans = [
        _span("llm:m", "llm", 300.0, reads=0, writes=0, db_ms=0.0),
        _span("tool:x", "tool", 50.0, reads=3, writes=1, db_ms=8.0),
    ]
    tr.add_root_db(write=False, ms=2.0)   # query solta do handler (fora de span)
    tr.add_root_db(write=True, ms=1.0)

    trace_rows, span_rows = sink._rows([tr])
    assert len(trace_rows) == 1
    assert len(span_rows) == 2
    row = trace_rows[0]
    # 4 do span (3r+1w) + 2 do handler = 6 queries; db_ms = 8 + 3
    assert row["db_queries"] == 6
    assert round(row["db_ms"], 1) == 11.0
    assert round(row["llm_ms"], 1) == 300.0  # só o span llm
    assert row["span_count"] == 2


def test_rows_offset_is_non_negative():
    tr = _trace()
    # span que começou "antes" do trace por jitter de clock não pode virar offset < 0
    tr.spans = [_span("s", "internal", 10.0, t0_offset=-0.5)]
    _, span_rows = sink._rows([tr])
    assert span_rows[0]["offset_ms"] >= 0.0


def test_keep_respects_disabled_via_submit(monkeypatch):
    """submit não enfileira quando não há loop/fila (estado inicial) — não levanta."""
    tr = _trace()
    sink.submit(tr)  # sem start(): _queue é None -> no-op silencioso
    assert sink.stats()["queued"] == 0
