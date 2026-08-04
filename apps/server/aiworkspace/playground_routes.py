"""Playground — rotas de Benchmarks, Comparações e Debug de Tools.

- Benchmarks: CRUD + disparo de execução (background) + histórico/poll de runs.
- Comparações: SSE de N modelos em paralelo (efêmero).
- Debug de Tools: catálogo, dispatch direto de uma tool, e trace SSE de um turno.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import budget_service
from .auth.deps import require_approved
from .db import get_db
from .models import Benchmark, BenchmarkRun, Tool, User
from .playground import compare as compare_service
from .playground import debug as debug_service
from .playground import runner as bench_runner
from .tools import sift_service

router = APIRouter(prefix="/playground", tags=["playground"])


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _sse_response(gen):
    async def _stream():
        async for ev in gen:
            yield _sse(ev)
    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tz(x_timezone: str | None = Header(default=None)) -> str:
    return (x_timezone or "").strip()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ModelRef(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str = ""
    model_config_id: str | None = None
    label: str = ""
    # suíte de DECISÃO: roda os casos como turno agêntico com as tools do preset
    # (exige model_config_id) — as regras tool_called/tool_not_called/no_tool
    # avaliam o que o modelo CHAMOU, não só o texto final
    tools: bool = False


class BenchmarkIn(BaseModel):
    name: str = ""
    description: str = ""
    cases: list[dict] = Field(default_factory=list)
    judge_model: str | None = None


class RunIn(BaseModel):
    models: list[ModelRef] = Field(default_factory=list)


class CompareIn(BaseModel):
    models: list[ModelRef] = Field(default_factory=list)
    prompt: str = ""
    system: str = ""


class DispatchIn(BaseModel):
    path: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class TraceIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str = ""
    model_config_id: str | None = None
    prompt: str = ""


# --------------------------------------------------------------------------- #
# Benchmarks
# --------------------------------------------------------------------------- #
async def _owned_benchmark(db: AsyncSession, bid: uuid.UUID, user: User) -> Benchmark:
    b = await db.get(Benchmark, bid)
    if b is None or b.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Benchmark não encontrado")
    return b


def _bench_summary(b: Benchmark) -> dict:
    return {
        "id": str(b.id),
        "name": b.name,
        "description": b.description,
        "case_count": len(b.cases or []),
        "judge_model": b.judge_model,
        "updated_at": b.updated_at,
    }


@router.get("/benchmarks")
async def list_benchmarks(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Benchmark).where(Benchmark.user_id == user.id).order_by(Benchmark.updated_at.desc())
    )
    return [_bench_summary(b) for b in rows]


@router.post("/benchmarks")
async def create_benchmark(body: BenchmarkIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = Benchmark(
        user_id=user.id,
        name=(body.name or "Novo benchmark")[:160],
        description=body.description or "",
        cases=_normalize_cases(body.cases),
        judge_model=(body.judge_model or None),
    )
    db.add(b)
    await db.commit()
    return {"id": str(b.id)}


@router.get("/benchmarks/{bid}")
async def get_benchmark(bid: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = await _owned_benchmark(db, bid, user)
    return {
        "id": str(b.id), "name": b.name, "description": b.description,
        "cases": b.cases or [], "judge_model": b.judge_model,
    }


@router.patch("/benchmarks/{bid}")
async def update_benchmark(bid: uuid.UUID, body: BenchmarkIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = await _owned_benchmark(db, bid, user)
    b.name = (body.name or b.name)[:160]
    b.description = body.description or ""
    b.cases = _normalize_cases(body.cases)
    b.judge_model = body.judge_model or None
    await db.commit()
    return {"ok": True}


@router.delete("/benchmarks/{bid}")
async def delete_benchmark(bid: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = await _owned_benchmark(db, bid, user)
    await db.delete(b)
    await db.commit()
    return {"ok": True}


def _normalize_cases(cases: list[dict]) -> list[dict]:
    """Garante um id em cada caso (para chavear os resultados) e enxuga os campos."""
    out: list[dict] = []
    for c in cases or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or uuid.uuid4().hex[:8])
        case: dict[str, Any] = {"id": cid, "prompt": str(c.get("prompt") or "")}
        if c.get("system"):
            case["system"] = str(c["system"])
        exp = c.get("expected")
        if isinstance(exp, dict):
            mode = exp.get("mode") or "none"
            # "no_tool" não tem valor (a regra é "não chamou NENHUMA tool real")
            if mode == "no_tool" or (mode != "none" and exp.get("value")):
                case["expected"] = {"mode": mode, "value": str(exp.get("value") or "")}
        if c.get("judge_criteria"):
            case["judge_criteria"] = str(c["judge_criteria"])
        out.append(case)
    return out


@router.post("/benchmarks/{bid}/run")
async def run_benchmark(bid: uuid.UUID, body: RunIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = await _owned_benchmark(db, bid, user)
    if not (b.cases or []):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adicione ao menos um caso de teste")
    if not body.models:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione ao menos um modelo")
    await budget_service.enforce_or_raise(db, user)
    run = BenchmarkRun(
        benchmark_id=b.id,
        user_id=user.id,
        models=[m.model_dump() for m in body.models],
        status="running",
        results={},
        aggregates={},
    )
    db.add(run)
    await db.commit()
    # bg.spawn (não create_task nu): sem referência forte o GC poderia coletar a task e o
    # benchmark ficaria "running" p/ sempre.
    from . import bg
    bg.spawn(bench_runner.run_benchmark(run.id))
    return {"run_id": str(run.id)}


@router.get("/benchmarks/{bid}/runs")
async def list_runs(bid: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await _owned_benchmark(db, bid, user)
    rows = await db.scalars(
        select(BenchmarkRun).where(BenchmarkRun.benchmark_id == bid).order_by(BenchmarkRun.created_at.desc())
    )
    return [
        {
            "id": str(r.id), "status": r.status, "models": r.models or [],
            "aggregates": r.aggregates or {}, "created_at": r.created_at, "error": r.error,
        }
        for r in rows
    ]


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    r = await db.get(BenchmarkRun, run_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execução não encontrada")
    b = await db.get(Benchmark, r.benchmark_id)
    return {
        "id": str(r.id), "status": r.status, "models": r.models or [],
        "results": r.results or {}, "aggregates": r.aggregates or {}, "error": r.error,
        "cases": (b.cases or []) if b else [],
        "judge_model": b.judge_model if b else None,
    }


# --------------------------------------------------------------------------- #
# Comparações
# --------------------------------------------------------------------------- #
@router.post("/compare")
async def compare(body: CompareIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    if len(body.models) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escolha ao menos 2 modelos")
    if not (body.prompt or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escreva um prompt")
    await budget_service.enforce_or_raise(db, user)
    from .chat.turn_setup import _get_model_config, _resolve_provider

    cols: list[dict] = []
    for m in body.models:
        mc = None
        if m.model_config_id:
            try:
                mc = await _get_model_config(db, uuid.UUID(str(m.model_config_id)), user)
            except (ValueError, TypeError):
                mc = None
        model_str = (mc.base_model if mc else m.model) or ""
        if not model_str:
            continue
        try:
            api_key, base_url = await _resolve_provider(db, user, model_str)
        except HTTPException:
            continue
        cols.append({
            "model": model_str, "api_key": api_key, "base_url": base_url,
            "params": (mc.params if mc else {}) or {},
            "system_prompt": (mc.system_prompt if mc else "") or "",
            "label": m.label or (mc.name if mc else model_str),
        })
    if len(cols) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhum par de modelos com provedor válido")
    # expõe os rótulos/modelos resolvidos ao front antes dos deltas
    async def _gen():
        yield {"type": "cols", "cols": [{"label": c["label"], "model": c["model"]} for c in cols]}
        async for ev in compare_service.stream_compare(cols, body.prompt, body.system):
            yield ev
    return _sse_response(_gen())


# --------------------------------------------------------------------------- #
# Debug de Tools
# --------------------------------------------------------------------------- #
@router.get("/tools")
async def tool_catalog(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    """Catálogo p/ o seletor do dispatch direto: builtins de sistema + tools do usuário."""
    builtins = [
        {"path": t["path"], "name": t["name"], "description": t.get("model_desc") or t["description"], "kind": "builtin", "params": {}}
        for t in sift_service.system_tools()
    ]
    rows = await db.scalars(select(Tool).where(Tool.user_id == user.id))
    user_tools = [
        {
            "path": sift_service.normalize_sift_path(t.path),
            "name": t.name or t.path,
            "description": t.description or "",
            "kind": "user",
            "params": t.params or {},
        }
        for t in rows
        if t.enabled and getattr(t, "tool_type", "code") != "mcp"
    ]
    return {"builtins": builtins, "user_tools": user_tools}


@router.post("/tool/dispatch")
async def tool_dispatch(body: DispatchIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    if not (body.path or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o caminho da ferramenta")
    return await debug_service.dispatch_tool(db, user, body.path.strip(), body.params or {})


@router.post("/tool/trace")
async def tool_trace(body: TraceIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db), user_tz: str = Depends(_tz)):
    if not (body.prompt or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escreva um prompt")
    await budget_service.enforce_or_raise(db, user)
    return _sse_response(
        debug_service.debug_turn(user, body.model, body.model_config_id, body.prompt, user_tz)
    )
