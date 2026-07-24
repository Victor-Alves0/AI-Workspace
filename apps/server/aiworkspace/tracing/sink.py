"""Escritor assíncrono em lote dos traces + podador de retenção.

Traces fechados são jogados numa fila em memória; uma task de fundo esvazia a fila
em lotes e grava no Postgres. A request NUNCA espera por isso.

Regras de sobrevivência — telemetria não pode derrubar o app que observa:
  - fila com teto: se encher (pico de tráfego, banco lento), novos traces são
    DESCARTADOS e um contador sobe. Perder telemetria é aceitável; travar não.
  - toda escrita é try/except: falha de banco vira log, não exceção que sobe.
  - o podador roda de tempos em tempos apagando traces além da retenção e acima do
    teto de linhas, para o rastro não crescer sem limite.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text

from ..config import get_settings
from ..db import SessionLocal
from ..models import ObsSpan, ObsTrace
from .context import Trace

logger = logging.getLogger("aiworkspace.tracing")

_QUEUE_MAX = 5000        # traces à espera de gravação
_BATCH = 200             # traces por flush
_FLUSH_INTERVAL = 1.0    # segundos entre flushes
_PRUNE_INTERVAL = 3600.0 # poda de hora em hora

_queue: asyncio.Queue[Trace] | None = None
_task: asyncio.Task | None = None
_prune_task: asyncio.Task | None = None
_loop: asyncio.AbstractEventLoop | None = None
_dropped = 0
_written = 0


def enabled() -> bool:
    return bool(getattr(get_settings(), "obs_enabled", True))


def _keep(tr: Trace) -> bool:
    """Decisão de amostragem. Erros e traces lentos são SEMPRE mantidos (é o que
    mais interessa depurar); o resto passa pela fração `obs_sample_rate`."""
    s = get_settings()
    if tr.status == "error":
        return True
    if tr.duration_ms >= float(getattr(s, "obs_slow_ms", 1500)):
        return True
    rate = float(getattr(s, "obs_sample_rate", 1.0))
    if rate >= 1.0:
        return True
    return random.random() < rate


def submit(tr: Trace) -> None:
    """Entrega um trace fechado para gravação. Chamado de dentro do `start_trace`
    (contexto async). Não bloqueia: se a fila está cheia, descarta."""
    global _dropped
    if _queue is None or _loop is None or not enabled():
        return
    if not _keep(tr):
        return
    try:
        _queue.put_nowait(tr)
    except asyncio.QueueFull:
        _dropped += 1


def stats() -> dict[str, int]:
    return {
        "queued": _queue.qsize() if _queue is not None else 0,
        "dropped": _dropped,
        "written": _written,
        "running": _task is not None and not _task.done(),
    }


async def start() -> None:
    """Sobe a fila e as tasks de flush/poda. Idempotente (lifespan)."""
    global _queue, _task, _prune_task, _loop
    if _task is not None and not _task.done():
        return
    _loop = asyncio.get_running_loop()
    _queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _task = asyncio.create_task(_run_flusher())
    _prune_task = asyncio.create_task(_run_pruner())
    logger.info("tracing sink iniciado")


async def stop() -> None:
    """Encerra as tasks e drena o que sobrou (best-effort)."""
    global _task, _prune_task
    for t in (_task, _prune_task):
        if t is not None:
            t.cancel()
    for t in (_task, _prune_task):
        if t is not None:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    _task = _prune_task = None
    await _drain_remaining()


# --------------------------------------------------------------------------- #
# Flush
# --------------------------------------------------------------------------- #

async def _run_flusher() -> None:
    assert _queue is not None
    while True:
        try:
            first = await _queue.get()
            batch = [first]
            # junta o que já está na fila até o teto do lote (uma transação só)
            while len(batch) < _BATCH:
                try:
                    batch.append(_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await _write_batch(batch)
            await asyncio.sleep(_FLUSH_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - nunca deixa a task morrer
            logger.warning("flush de tracing falhou", exc_info=True)
            await asyncio.sleep(_FLUSH_INTERVAL)


def _rows(batch: list[Trace]) -> tuple[list[dict], list[dict]]:
    """Converte traces+spans em linhas para bulk insert (mappings)."""
    trace_rows: list[dict] = []
    span_rows: list[dict] = []
    for tr in batch:
        started = datetime.fromtimestamp(tr.started_wall, tz=timezone.utc)
        # totais do trace = trabalho dos spans + trabalho feito direto no handler
        # (fora de span), senão as queries "soltas" da rota não apareceriam
        db_ms = sum(s.db_ms for s in tr.spans) + tr.root_db_ms
        db_q = (sum(s.db_reads + s.db_writes for s in tr.spans)
                + tr.root_db_reads + tr.root_db_writes)
        http_ms = sum(s.http_ms for s in tr.spans)
        llm_ms = sum(s.duration_ms for s in tr.spans if s.kind == "llm")
        trace_rows.append({
            "id": tr.id, "user_id": tr.user_id, "name": tr.name, "kind": tr.kind,
            "method": tr.method, "path": tr.path, "status_code": tr.status_code,
            "status": tr.status, "error": tr.error, "started_at": started,
            "duration_ms": tr.duration_ms, "span_count": len(tr.spans),
            "db_ms": round(db_ms, 3), "db_queries": db_q, "http_ms": round(http_ms, 3),
            "llm_ms": round(llm_ms, 3), "attrs": tr.attrs,
        })
        for s in tr.spans:
            offset = max(0.0, round((s.started_wall - tr.started_wall) * 1000, 3))
            span_rows.append({
                "id": s.id, "trace_id": tr.id, "parent_id": s.parent_id,
                "name": s.name, "kind": s.kind,
                "started_at": datetime.fromtimestamp(s.started_wall, tz=timezone.utc),
                "offset_ms": offset, "duration_ms": s.duration_ms, "status": s.status,
                "error": s.error, "db_reads": s.db_reads, "db_writes": s.db_writes,
                "db_ms": round(s.db_ms, 3), "http_ms": round(s.http_ms, 3),
                "attrs": s.attrs,
            })
    return trace_rows, span_rows


async def _write_batch(batch: list[Trace]) -> None:
    global _written
    trace_rows, span_rows = _rows(batch)
    if not trace_rows:
        return
    # IMPORTANTE: as escritas do sink rodam numa sessão PRÓPRIA e não são
    # instrumentadas (o instrument checa este flag) — senão o tracing rastrearia a
    # si mesmo num laço.
    try:
        async with SessionLocal() as db:
            await db.execute(ObsTrace.__table__.insert(), trace_rows)
            if span_rows:
                await db.execute(ObsSpan.__table__.insert(), span_rows)
            await db.commit()
        _written += len(trace_rows)
    except Exception:  # noqa: BLE001
        logger.warning("gravação de %d traces falhou", len(trace_rows), exc_info=True)


async def _drain_remaining() -> None:
    if _queue is None:
        return
    leftover: list[Trace] = []
    while True:
        try:
            leftover.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    if leftover:
        await _write_batch(leftover)


# --------------------------------------------------------------------------- #
# Retenção
# --------------------------------------------------------------------------- #

async def _run_pruner() -> None:
    while True:
        try:
            await asyncio.sleep(_PRUNE_INTERVAL)
            await prune_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("poda de tracing falhou", exc_info=True)


async def prune_once() -> int:
    """Apaga traces além da retenção (dias) e acima do teto de linhas. Spans somem
    por CASCADE. Devolve quantos traces foram apagados."""
    s = get_settings()
    days = int(getattr(s, "obs_retention_days", 14) or 14)
    max_rows = int(getattr(s, "obs_max_traces", 500_000) or 0)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    try:
        async with SessionLocal() as db:
            res = await db.execute(
                delete(ObsTrace).where(ObsTrace.started_at < cutoff)
            )
            removed += res.rowcount or 0
            if max_rows > 0:
                total = await db.scalar(select(text("count(*)")).select_from(ObsTrace.__table__))
                excess = int(total or 0) - max_rows
                if excess > 0:
                    # apaga os mais antigos além do teto
                    ids = (await db.execute(
                        select(ObsTrace.id).order_by(ObsTrace.started_at.asc()).limit(excess)
                    )).scalars().all()
                    if ids:
                        r2 = await db.execute(delete(ObsTrace).where(ObsTrace.id.in_(ids)))
                        removed += r2.rowcount or 0
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("prune_once falhou", exc_info=True)
    if removed:
        logger.info("tracing: %d traces podados", removed)
    return removed
