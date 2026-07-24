"""Leitura da observabilidade — somente admin.

Consulta os traces/spans persistidos: lista filtrável, o waterfall de um trace,
painéis agregados (latência por rota, tempo de banco, taxa de erro) e o estado ao
vivo do sink. Também recebe o beacon de tempos do cliente (do clique ao primeiro
byte) e o correlaciona ao trace do servidor pelo `X-Trace-Id`.

Fica FORA do tracing (o middleware não rastreia `/observability`): ler o rastro não
pode gerar mais rastro nem contar como carga.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_admin, require_approved
from .config import get_settings
from .db import get_db
from .models import ObsSpan, ObsTrace, User
from .tracing import sink

router = APIRouter(prefix="/observability", tags=["observability"])


# --------------------------------------------------------------------------- #
# Configuração e estado
# --------------------------------------------------------------------------- #

@router.get("/config")
async def config(admin: User = Depends(require_admin)):
    s = get_settings()
    return {
        "enabled": s.obs_enabled,
        "sample_rate": s.obs_sample_rate,
        "retention_days": s.obs_retention_days,
        "max_traces": s.obs_max_traces,
        "capture_content": s.obs_capture_content,
        "slow_ms": s.obs_slow_ms,
        "sink": sink.stats(),
    }


@router.post("/prune")
async def prune(admin: User = Depends(require_admin)):
    """Roda a poda de retenção na hora (além do ciclo automático de 1h)."""
    removed = await sink.prune_once()
    return {"removed": removed}


# --------------------------------------------------------------------------- #
# Lista de traces
# --------------------------------------------------------------------------- #

def _trace_row(t: ObsTrace) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "kind": t.kind,
        "method": t.method,
        "path": t.path,
        "status": t.status,
        "status_code": t.status_code,
        "error": t.error,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "duration_ms": round(t.duration_ms, 2),
        "span_count": t.span_count,
        "db_ms": round(t.db_ms, 2),
        "db_queries": t.db_queries,
        "http_ms": round(t.http_ms, 2),
        "llm_ms": round(t.llm_ms, 2),
        "user_id": str(t.user_id) if t.user_id else None,
        "attrs": t.attrs or {},
    }


@router.get("/traces")
async def list_traces(
    kind: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    path: str | None = None,
    q: str | None = None,
    user_id: str | None = None,
    min_ms: float = 0,
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    stmt = select(ObsTrace).where(ObsTrace.started_at >= since)
    if kind:
        stmt = stmt.where(ObsTrace.kind == kind)
    if status_filter in ("ok", "error"):
        stmt = stmt.where(ObsTrace.status == status_filter)
    if path:
        stmt = stmt.where(ObsTrace.path.ilike(f"%{path}%"))
    if q:
        stmt = stmt.where(ObsTrace.name.ilike(f"%{q}%"))
    if user_id:
        try:
            stmt = stmt.where(ObsTrace.user_id == uuid.UUID(user_id))
        except ValueError:
            pass
    if min_ms > 0:
        stmt = stmt.where(ObsTrace.duration_ms >= min_ms)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(await db.scalars(
        stmt.order_by(ObsTrace.started_at.desc()).offset(offset).limit(limit)
    ))
    return {"total": int(total or 0), "traces": [_trace_row(t) for t in rows]}


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    t = await db.get(ObsTrace, trace_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trace não encontrado")
    spans = list(await db.scalars(
        select(ObsSpan).where(ObsSpan.trace_id == trace_id).order_by(ObsSpan.offset_ms)
    ))
    return {
        "trace": _trace_row(t),
        "spans": [
            {
                "id": str(s.id),
                "parent_id": str(s.parent_id) if s.parent_id else None,
                "name": s.name,
                "kind": s.kind,
                "offset_ms": round(s.offset_ms, 2),
                "duration_ms": round(s.duration_ms, 2),
                "status": s.status,
                "error": s.error,
                "db_reads": s.db_reads,
                "db_writes": s.db_writes,
                "db_ms": round(s.db_ms, 2),
                "http_ms": round(s.http_ms, 2),
                "attrs": s.attrs or {},
            }
            for s in spans
        ],
    }


# --------------------------------------------------------------------------- #
# Agregados / dashboards
# --------------------------------------------------------------------------- #

@router.get("/summary")
async def summary(
    hours: int = Query(24, ge=1, le=720),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Visão geral do período: totais, latência (p50/p95/p99), erro, tempo de banco."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = ObsTrace.started_at >= since

    totals = (await db.execute(
        select(
            func.count(),
            func.count().filter(ObsTrace.status == "error"),
            func.coalesce(func.avg(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.percentile_cont(0.5).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.percentile_cont(0.95).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.percentile_cont(0.99).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.sum(ObsTrace.db_queries), 0),
            func.coalesce(func.avg(ObsTrace.db_ms), 0.0),
            func.coalesce(func.sum(ObsTrace.llm_ms), 0.0),
        ).where(base)
    )).one()

    # por rota: contagem, p95, taxa de erro, tempo médio de banco — o que aponta o
    # gargalo sem abrir trace por trace
    routes = (await db.execute(
        select(
            ObsTrace.kind, ObsTrace.method, ObsTrace.path,
            func.count(),
            func.count().filter(ObsTrace.status == "error"),
            func.coalesce(func.percentile_cont(0.95).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.db_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.db_queries), 0.0),
        )
        .where(base)
        .group_by(ObsTrace.kind, ObsTrace.method, ObsTrace.path)
        .order_by(func.count().desc())
        .limit(50)
    )).all()

    # série temporal por bucket de hora: volume e erros
    bucket = func.date_trunc("hour", ObsTrace.started_at)
    series = (await db.execute(
        select(bucket, func.count(), func.count().filter(ObsTrace.status == "error"),
               func.coalesce(func.avg(ObsTrace.duration_ms), 0.0))
        .where(base).group_by(bucket).order_by(bucket)
    )).all()

    return {
        "period_hours": hours,
        "totals": {
            "traces": int(totals[0] or 0),
            "errors": int(totals[1] or 0),
            "avg_ms": round(float(totals[2] or 0), 2),
            "p50_ms": round(float(totals[3] or 0), 2),
            "p95_ms": round(float(totals[4] or 0), 2),
            "p99_ms": round(float(totals[5] or 0), 2),
            "db_queries": int(totals[6] or 0),
            "avg_db_ms": round(float(totals[7] or 0), 2),
            "llm_ms": round(float(totals[8] or 0), 2),
        },
        "routes": [
            {
                "kind": k, "method": m, "path": p, "count": int(c or 0),
                "errors": int(e or 0), "p95_ms": round(float(p95 or 0), 2),
                "avg_ms": round(float(avg or 0), 2), "avg_db_ms": round(float(dbms or 0), 2),
                "avg_queries": round(float(dq or 0), 1),
            }
            for k, m, p, c, e, p95, avg, dbms, dq in routes
        ],
        "series": [
            {"hour": h.isoformat(), "count": int(c or 0), "errors": int(e or 0),
             "avg_ms": round(float(a or 0), 2)}
            for h, c, e, a in series
        ],
    }


@router.get("/slowest")
async def slowest(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Traces mais lentos do período — o ponto de partida de qualquer investigação."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = list(await db.scalars(
        select(ObsTrace).where(ObsTrace.started_at >= since)
        .order_by(ObsTrace.duration_ms.desc()).limit(limit)
    ))
    return {"traces": [_trace_row(t) for t in rows]}


# --------------------------------------------------------------------------- #
# Beacon do cliente (RUM leve)
# --------------------------------------------------------------------------- #

class ClientBeacon(BaseModel):
    # tudo aqui vem do navegador: limita no parse, não só no corte da gravação
    trace_id: str | None = Field(None, max_length=64)  # X-Trace-Id devolvido pelo servidor
    action: str = Field(max_length=120)  # "login-click", "chat-send", "navigate", ...
    path: str = Field("", max_length=300)
    # tempos do lado do cliente (ms)
    total_ms: float = 0
    ttfb_ms: float = 0                # até o 1º byte
    render_ms: float = 0             # até re-render, quando aplicável
    ok: bool = True


@router.post("/client")
async def client_beacon(
    beacon: ClientBeacon,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Anexa os tempos medidos no NAVEGADOR ao trace do servidor como um span
    `client`. É assim que o rastro cobre 'do clique ao oi': o clique e a rede vivem
    no cliente; o resto, no servidor. Sem trace correlacionável, guarda um trace
    curto só-cliente.

    O `trace_id` vem do cliente, então é ENTRADA NÃO CONFIÁVEL: só anexamos a um
    trace que seja do PRÓPRIO usuário. Sem essa checagem, qualquer usuário logado
    podia injetar spans com texto escolhido por ele no trace de outro (inclusive do
    admin), poluindo o painel de observabilidade."""
    from .models import ObsSpan as _Span

    now = datetime.now(timezone.utc)
    attrs = {"action": beacon.action[:120], "ttfb_ms": round(beacon.ttfb_ms, 1),
             "render_ms": round(beacon.render_ms, 1)}

    tid: uuid.UUID | None = None
    if beacon.trace_id:
        try:
            tid = uuid.UUID(beacon.trace_id)
        except ValueError:
            tid = None
    target = await db.get(ObsTrace, tid) if tid is not None else None
    if target is not None and str(target.user_id) != str(user.id):
        target = None  # trace de outra pessoa: cai no trace só-cliente abaixo
    if target is not None:
        db.add(_Span(
            trace_id=tid, parent_id=None, name=f"client:{beacon.action}"[:200],
            kind="client", started_at=now, offset_ms=0.0,
            duration_ms=round(beacon.total_ms, 2),
            status="ok" if beacon.ok else "error", db_reads=0, db_writes=0,
            attrs=attrs,
        ))
        await db.commit()
        return {"ok": True, "attached": True}

    # sem trace do servidor: registra um trace curto só do cliente
    tr = ObsTrace(
        user_id=user.id, name=f"client:{beacon.action}"[:200], kind="client",
        method="", path=beacon.path[:300], status="ok" if beacon.ok else "error",
        started_at=now, duration_ms=round(beacon.total_ms, 2), span_count=0,
        attrs=attrs,
    )
    db.add(tr)
    await db.commit()
    return {"ok": True, "attached": False}
