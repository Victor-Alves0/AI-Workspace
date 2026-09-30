"""Saúde do PROCESSO em tempo real: atraso do event loop, travadas e o que estava
rodando nelas, tarefas, pool do banco, memória.

O atraso do loop é o gargalo mais traiçoeiro de um servidor async: um trecho
síncrono pesado (embeddings ONNX, parse de PDF, regex gigante) congela TODAS as
requisições ao mesmo tempo, e nenhum span mostra isso — cada um só vê que "demorou".
Aqui uma task acorda a cada `_TICK` e mede quanto atrasou. Atraso ≥ `_STALL_MS` vira
um trace `runtime:loop-stall` com os traces que estavam abertos naquele instante
(os suspeitos), então aparece no painel junto com o resto.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import deque
from typing import Any

from .context import _open_traces, new_trace

logger = logging.getLogger("aiworkspace.tracing")

_TICK = 0.25                 # s entre medições
_STALL_MS = 250.0            # atraso que conta como "travada"
_WINDOW = 15 * 60            # s de histórico em memória
_samples: deque[tuple[float, float]] = deque(maxlen=int(_WINDOW / _TICK))
_stalls: deque[dict[str, Any]] = deque(maxlen=200)
_task: asyncio.Task | None = None
_started_at = time.time()


def start() -> None:
    """Sobe o medidor (idempotente). Precisa de loop rodando (lifespan)."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.get_running_loop().create_task(_run(), name="obs-loop-lag")


async def _run() -> None:
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(_TICK)
        lag = max(0.0, (loop.time() - t0 - _TICK) * 1000)
        agora = time.time()
        _samples.append((agora, lag))
        if lag >= _STALL_MS:
            try:
                _record_stall(agora, lag)
            except Exception:  # noqa: BLE001 - medidor nunca morre
                pass


def _record_stall(when: float, lag_ms: float) -> None:
    abertos = sorted(_open_traces.values(), key=lambda t: t.started_wall)
    suspeitos = []
    for tr in abertos[:12]:
        ultimo = tr.spans[-1].name if tr.spans else ""
        suspeitos.append({"trace": tr.id, "name": tr.name,
                          "age_ms": round((when - tr.started_wall) * 1000), "last_span": ultimo})
    item = {"at": when, "lag_ms": round(lag_ms, 1), "suspects": suspeitos}
    _stalls.append(item)
    # vira trace persistido (fora de qualquer contexto: não herda pai)
    from . import sink

    tr = new_trace("runtime:loop-stall", kind="runtime", lag_ms=round(lag_ms, 1),
                   suspects=suspeitos)
    tr.started_wall = when - lag_ms / 1000
    tr.duration_ms = round(lag_ms, 1)
    tr.closed = True
    tr.status = "error" if lag_ms >= 1000 else "ok"
    if tr.status == "error":
        tr.error = f"event loop travado por {lag_ms / 1000:.1f}s"
    sink.submit(tr)


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
    return round(vals[k], 1)


def _lag_window(seconds: float) -> dict[str, float]:
    lim = time.time() - seconds
    vals = [lag for t, lag in _samples if t >= lim]
    return {"p50": _pct(vals, 0.5), "p95": _pct(vals, 0.95), "p99": _pct(vals, 0.99),
            "max": round(max(vals), 1) if vals else 0.0, "samples": len(vals)}


def _memory_mb() -> float | None:
    try:
        import psutil  # type: ignore[import-not-found]

        return round(psutil.Process().memory_info().rss / 1_048_576, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for linha in fh:
                if linha.startswith("VmRSS:"):
                    return round(int(linha.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def _cpu_seconds() -> float | None:
    try:
        t = os.times()
        return round(t.user + t.system, 2)
    except Exception:  # noqa: BLE001
        return None


def _db_pool() -> dict[str, Any]:
    try:
        from ..db import engine

        pool = engine.sync_engine.pool
        out: dict[str, Any] = {"status": pool.status()}
        for k in ("size", "checkedout", "checkedin", "overflow"):
            fn = getattr(pool, k, None)
            if callable(fn):
                out[k] = fn()
        return out
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def snapshot() -> dict[str, Any]:
    """Foto do processo agora (para /observability/runtime)."""
    from .. import bg

    agora = time.time()
    abertos = sorted(_open_traces.values(), key=lambda t: t.started_wall)
    try:
        from ..chat import generation

        gens = sum(1 for g in generation._active.values() if not g.done)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        gens = None
    try:
        tasks = len(asyncio.all_tasks())
    except RuntimeError:
        tasks = None
    return {
        "uptime_s": round(agora - _started_at),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "memory_mb": _memory_mb(),
        "cpu_s": _cpu_seconds(),
        "loop_lag": {"1m": _lag_window(60), "5m": _lag_window(300), "15m": _lag_window(_WINDOW)},
        "lag_series": [{"t": round(t), "ms": round(lag, 1)} for t, lag in list(_samples)[-240:]],
        "stalls": [dict(s) for s in list(_stalls)[-30:]][::-1],
        "open_traces": [
            {"id": t.id, "name": t.name, "kind": t.kind, "age_ms": round((agora - t.started_wall) * 1000),
             "spans": len(t.spans), "last_span": t.spans[-1].name if t.spans else "",
             "parent_trace": t.attrs.get("parent_trace")}
            for t in abertos[:60]
        ],
        "tasks": tasks,
        "bg_tasks": len(bg._TASKS),  # noqa: SLF001
        "active_generations": gens,
        "db_pool": _db_pool(),
    }
