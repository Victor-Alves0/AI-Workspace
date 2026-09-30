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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_admin, require_approved
from .config import get_settings
from .db import get_db
from .models import ObsSpan, ObsTrace, User
from . import tracing
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
    # CADEIA: quem disparou este trace e o que ele disparou (geração a partir do
    # POST, equipe em 2º plano, turno acordado…) — navegável no painel
    parent = None
    pid = (t.attrs or {}).get("parent_trace")
    if pid:
        try:
            pt = await db.get(ObsTrace, uuid.UUID(str(pid)))
        except ValueError:
            pt = None
        if pt is not None:
            parent = _trace_row(pt)
    kids = list(await db.scalars(
        select(ObsTrace).where(ObsTrace.attrs["parent_trace"].astext == trace_id.hex)
        .order_by(ObsTrace.started_at).limit(100)
    ))
    return {
        "trace": _trace_row(t),
        "parent": parent,
        "children": [_trace_row(k) for k in kids],
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
    # operações sem rota (geração, 2º plano, travadas) agrupam pelo NOME
    rota = func.coalesce(func.nullif(ObsTrace.path, ""), ObsTrace.name)
    routes = (await db.execute(
        select(
            ObsTrace.kind, ObsTrace.method, rota,
            func.count(),
            func.count().filter(ObsTrace.status == "error"),
            func.coalesce(func.percentile_cont(0.95).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.db_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.db_queries), 0.0),
            func.coalesce(func.percentile_cont(0.5).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.percentile_cont(0.99).within_group(ObsTrace.duration_ms), 0.0),
            func.coalesce(func.avg(ObsTrace.llm_ms), 0.0),
            func.coalesce(func.sum(ObsTrace.duration_ms), 0.0),
        )
        .where(base)
        .group_by(ObsTrace.kind, ObsTrace.method, rota)
        .order_by(func.sum(ObsTrace.duration_ms).desc())
        .limit(80)
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
                "p50_ms": round(float(p50 or 0), 2), "p99_ms": round(float(p99 or 0), 2),
                "avg_llm_ms": round(float(llm or 0), 2), "total_ms": round(float(tot or 0), 2),
            }
            for k, m, p, c, e, p95, avg, dbms, dq, p50, p99, llm, tot in routes
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
    # O beacon de primeiro token chega enquanto um trace de geração ainda está
    # aberto, antes de o sink fazer o batch no Postgres. Anexa diretamente ao
    # trace em memória para ele ser persistido junto no ``done``; assim TTFT não
    # cai num trace avulso por uma corrida normal do streaming.
    active = tracing.get_open_trace(beacon.trace_id) if beacon.trace_id else None
    if active is not None and str(active.user_id) == str(user.id):
        # O TTFT do browser também inclui o setup HTTP anterior ao driver. Para
        # posicionar o span no waterfall do trace de geração, use o TTFT do
        # servidor (já anotado antes de emitir o token); o valor do navegador
        # permanece em attrs para mostrar a diferença rede/renderização.
        server_offset_ms = float(active.attrs.get("server_ttft_ms") or beacon.ttfb_ms or 0)
        started_wall = active.started_wall + max(0.0, server_offset_ms) / 1000
        client_span = tracing.Span(
            id=uuid.uuid4().hex, trace_id=active.id, parent_id=None,
            name=f"client:{beacon.action}"[:200], kind="client",
            started_wall=started_wall, _t0=0.0,
            duration_ms=round(beacon.total_ms, 2),
            status="ok" if beacon.ok else "error", attrs=attrs,
        )
        active.add_span(client_span)
        return {"ok": True, "attached": True, "pending": True}

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


# --------------------------------------------------------------------------- #
# Análise de gargalos
# --------------------------------------------------------------------------- #

def _f(v: Any) -> float:
    return round(float(v or 0), 2)


@router.get("/operations")
async def operations(
    hours: int = Query(24, ge=1, le=720),
    trace_kind: str | None = None,
    path: str | None = None,
    span_kind: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Cada ETAPA (span) agregada pelo nome no período: quantas vezes rodou, p50/p95/
    p99/máx, tempo TOTAL gasto nela, tempo PRÓPRIO (sem os filhos), banco e rede.
    Ordenado pelo tempo total — o topo é onde o sistema passa mais tempo."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    cond = ["sp.started_at >= :since"]
    params: dict[str, Any] = {"since": since, "lim": limit}
    if trace_kind:
        cond.append("t.kind = :tk")
        params["tk"] = trace_kind
    if path:
        cond.append("coalesce(nullif(t.path, ''), t.name) = :path")
        params["path"] = path
    if span_kind:
        cond.append("sp.kind = :sk")
        params["sk"] = span_kind
    if q:
        cond.append("sp.name ILIKE :q")
        params["q"] = f"%{q}%"
    where = " AND ".join(cond)
    rows = (await db.execute(text(f"""
        WITH s AS (
            SELECT sp.id, sp.name, sp.kind, sp.duration_ms, sp.status, sp.db_ms, sp.http_ms,
                   sp.db_reads + sp.db_writes AS q
            FROM obs_spans sp JOIN obs_traces t ON t.id = sp.trace_id
            WHERE {where}
        ), c AS (
            SELECT parent_id, sum(duration_ms) AS child_ms
            FROM obs_spans WHERE started_at >= :since AND parent_id IS NOT NULL
            GROUP BY parent_id
        )
        SELECT s.name, s.kind, count(*) AS n,
               count(*) FILTER (WHERE s.status = 'error') AS errs,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY s.duration_ms),
               percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms),
               percentile_cont(0.99) WITHIN GROUP (ORDER BY s.duration_ms),
               max(s.duration_ms), sum(s.duration_ms), avg(s.db_ms), avg(s.http_ms), avg(s.q),
               avg(greatest(s.duration_ms - coalesce(c.child_ms, 0), 0)),
               sum(greatest(s.duration_ms - coalesce(c.child_ms, 0), 0))
        FROM s LEFT JOIN c ON c.parent_id = s.id
        GROUP BY s.name, s.kind
        ORDER BY sum(s.duration_ms) DESC
        LIMIT :lim
    """), params)).all()
    total_self = sum(float(r[13] or 0) for r in rows) or 1.0
    return {
        "period_hours": hours,
        "operations": [
            {"name": r[0], "kind": r[1], "count": int(r[2]), "errors": int(r[3]),
             "p50_ms": _f(r[4]), "p95_ms": _f(r[5]), "p99_ms": _f(r[6]), "max_ms": _f(r[7]),
             "total_ms": _f(r[8]), "avg_db_ms": _f(r[9]), "avg_http_ms": _f(r[10]),
             "avg_queries": round(float(r[11] or 0), 1), "avg_self_ms": _f(r[12]),
             "self_total_ms": _f(r[13]), "self_share": round(float(r[13] or 0) / total_self, 4)}
            for r in rows
        ],
    }


def _num_stats(vals: list[float]) -> dict[str, float]:
    vals = sorted(vals)
    n = len(vals)

    def pct(p: float) -> float:
        return round(vals[min(n - 1, max(0, int(round(p * (n - 1)))))], 2)

    return {"n": n, "avg": round(sum(vals) / n, 2), "p50": pct(0.5), "p95": pct(0.95), "max": round(vals[-1], 2)}


@router.get("/operations/detail")
async def operation_detail(
    name: str,
    hours: int = Query(24, ge=1, le=720),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Uma etapa por dentro: os números que ELA registra (1º byte, tokens/s, tamanho do
    contexto, fila do pool, cache…) com média/p50/p95, os caminhos/hosts mais lentos,
    os erros mais comuns e as execuções mais lentas (para abrir o trace)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    spans = list(await db.scalars(
        select(ObsSpan).where(ObsSpan.started_at >= since, ObsSpan.name == name)
        .order_by(ObsSpan.started_at.desc()).limit(3000)
    ))
    if not spans:
        return {"name": name, "count": 0, "duration": None, "attrs": {}, "groups": [],
                "errors": [], "slowest": []}
    durs = [float(s.duration_ms or 0) for s in spans]
    numeric: dict[str, list[float]] = {}
    grupos: dict[str, list[float]] = {}
    erros: dict[str, int] = {}
    for s in spans:
        a = s.attrs or {}
        for k, v in a.items():
            if k == "queries" or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                numeric.setdefault(k, []).append(float(v))
        # agrupador natural de cada tipo: caminho (http), ferramenta interna, motor
        chave = a.get("path") or a.get("inner") or a.get("backend") or a.get("provider") or ""
        if chave:
            grupos.setdefault(str(chave), []).append(float(s.duration_ms or 0))
        if s.status == "error":
            msg = (s.error or "erro")[:160]
            erros[msg] = erros.get(msg, 0) + 1
    slow = sorted(spans, key=lambda s: s.duration_ms or 0, reverse=True)[:25]
    return {
        "name": name,
        "count": len(spans),
        "duration": _num_stats(durs),
        "attrs": {k: _num_stats(v) for k, v in sorted(numeric.items())},
        "groups": sorted(
            ({"key": k, **_num_stats(v)} for k, v in grupos.items()),
            key=lambda g: g["avg"] * g["n"], reverse=True)[:30],
        "errors": sorted(({"error": k, "count": c} for k, c in erros.items()),
                         key=lambda e: e["count"], reverse=True)[:15],
        "slowest": [
            {"trace_id": str(s.trace_id), "span_id": str(s.id), "duration_ms": round(s.duration_ms, 2),
             "started_at": s.started_at.isoformat() if s.started_at else None, "status": s.status,
             "attrs": {k: v for k, v in (s.attrs or {}).items() if k != "queries"}}
            for s in slow
        ],
    }


_FAIXAS = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000]


@router.get("/route")
async def route_detail(
    path: str,
    method: str = "",
    kind: str | None = None,
    hours: int = Query(24, ge=1, le=720),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Um endpoint (ou tipo de operação) por dentro: latência, histograma, e ONDE o
    tempo de cada chamada vai — média por etapa por chamada + o tempo que não está em
    nenhuma etapa medida (o próprio handler). Inclui o que ele dispara (ex.: a geração
    que o POST de mensagem solta)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = [ObsTrace.started_at >= since,
            func.coalesce(func.nullif(ObsTrace.path, ""), ObsTrace.name) == path]
    if method:
        base.append(ObsTrace.method == method)
    if kind:
        base.append(ObsTrace.kind == kind)
    tot = (await db.execute(select(
        func.count(), func.count().filter(ObsTrace.status == "error"),
        func.percentile_cont(0.5).within_group(ObsTrace.duration_ms),
        func.percentile_cont(0.95).within_group(ObsTrace.duration_ms),
        func.percentile_cont(0.99).within_group(ObsTrace.duration_ms),
        func.max(ObsTrace.duration_ms), func.avg(ObsTrace.duration_ms),
        func.avg(ObsTrace.db_ms), func.avg(ObsTrace.db_queries), func.avg(ObsTrace.llm_ms),
        func.avg(ObsTrace.http_ms),
    ).where(*base))).one()
    n = int(tot[0] or 0)
    if n == 0:
        return {"path": path, "method": method, "count": 0, "breakdown": [], "histogram": [],
                "slowest": [], "children": []}
    params = {"since": since, "path": path, "method": method or None, "kind": kind or None}
    filtro = ("t.started_at >= :since AND coalesce(nullif(t.path, ''), t.name) = :path"
              " AND (CAST(:method AS text) IS NULL OR t.method = CAST(:method AS text))"
              " AND (CAST(:kind AS text) IS NULL OR t.kind = CAST(:kind AS text))")
    brk = (await db.execute(text(f"""
        SELECT sp.name, sp.kind, count(*) AS n, count(DISTINCT sp.trace_id) AS traces,
               sum(sp.duration_ms), percentile_cont(0.95) WITHIN GROUP (ORDER BY sp.duration_ms),
               bool_or(sp.parent_id IS NULL)
        FROM obs_spans sp JOIN obs_traces t ON t.id = sp.trace_id
        WHERE {filtro}
        GROUP BY sp.name, sp.kind
        ORDER BY sum(sp.duration_ms) DESC LIMIT 40
    """), params)).all()
    topo = (await db.execute(text(f"""
        SELECT coalesce(sum(sp.duration_ms), 0)
        FROM obs_spans sp JOIN obs_traces t ON t.id = sp.trace_id
        WHERE {filtro} AND sp.parent_id IS NULL AND sp.kind <> 'client'
    """), params)).scalar() or 0.0
    soma_total = float(tot[6] or 0) * n
    limites = ",".join(str(f) for f in _FAIXAS)
    hist = (await db.execute(text(f"""
        SELECT width_bucket(t.duration_ms, ARRAY[{limites}]::float8[]) AS b, count(*)
        FROM obs_traces t WHERE {filtro} GROUP BY b ORDER BY b
    """), params)).all()
    rot = ["<50ms"] + [
        f"{a}–{b}ms" if b <= 1000 else f"{a / 1000:g}–{b / 1000:g}s"
        for a, b in zip(_FAIXAS, _FAIXAS[1:])
    ] + [f"≥{_FAIXAS[-1] / 1000:g}s"]
    slow = list(await db.scalars(
        select(ObsTrace).where(*base).order_by(ObsTrace.duration_ms.desc()).limit(15)))
    kids = (await db.execute(text(f"""
        SELECT c.name, c.kind, count(*), avg(c.duration_ms),
               percentile_cont(0.95) WITHIN GROUP (ORDER BY c.duration_ms)
        FROM obs_traces c
        JOIN obs_traces t ON c.attrs->>'parent_trace' = replace(CAST(t.id AS text), '-', '')
        WHERE {filtro}
        GROUP BY c.name, c.kind ORDER BY count(*) DESC LIMIT 20
    """), params)).all()
    return {
        "path": path, "method": method, "count": n, "errors": int(tot[1] or 0),
        "p50_ms": _f(tot[2]), "p95_ms": _f(tot[3]), "p99_ms": _f(tot[4]), "max_ms": _f(tot[5]),
        "avg_ms": _f(tot[6]), "avg_db_ms": _f(tot[7]), "avg_queries": round(float(tot[8] or 0), 1),
        "avg_llm_ms": _f(tot[9]), "avg_http_ms": _f(tot[10]),
        # tempo médio por chamada que NÃO está em nenhuma etapa de 1º nível
        "avg_unaccounted_ms": _f(max(0.0, soma_total - float(topo)) / n),
        "breakdown": [
            {"name": r[0], "kind": r[1], "count": int(r[2]), "traces": int(r[3]),
             "per_call_ms": _f(float(r[4] or 0) / n), "p95_ms": _f(r[5]),
             "calls_per_trace": round(int(r[2]) / max(1, int(r[3])), 2), "top_level": bool(r[6])}
            for r in brk
        ],
        "histogram": [{"bucket": rot[min(int(b or 0), len(rot) - 1)], "count": int(c)} for b, c in hist],
        "slowest": [_trace_row(t) for t in slow],
        "children": [
            {"name": r[0], "kind": r[1], "count": int(r[2]), "avg_ms": _f(r[3]), "p95_ms": _f(r[4])}
            for r in kids
        ],
    }


@router.get("/runtime")
async def runtime_state(admin: User = Depends(require_admin)):
    """O processo AGORA: atraso do event loop (travadas e suspeitos), traces abertos,
    tarefas, gerações ativas, pool do banco, memória — e a fila do gravador."""
    from .tracing import runtime

    return {**runtime.snapshot(), "sink": sink.stats()}
