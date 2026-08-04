"""Endpoints de gerência da API: memória, arquivos, uso e conta.

São os recursos que uma aplicação integrada precisa administrar sozinha, sem
depender de alguém abrir a interface: o que o agente lembra dela, quais documentos
ele consulta, quanto já gastou e quanto ainda pode gastar.

Cada rota exige o escopo correspondente da chave — `memory:write` para gravar,
`files:write` para subir documento e assim por diante.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..budget_service import budget_state
from ..knowledge import ingest
from ..memory import mem0_service
from ..models import ApiRequest, KnowledgeBase, KnowledgeDoc, User
from ..secrets_service import OPENROUTER_KEY, get_secret
from . import keys_service, limits
from .deps import ApiContext, ApiError, scoped

router = APIRouter(prefix="/v1", tags=["api"])

_MAX_UPLOAD = 50 * 1024 * 1024


async def _mem_key(db: AsyncSession, user: User) -> str:
    """Chave usada pelo mem0. Operações de gerência não chamam o LLM, então um
    placeholder serve quando o usuário ainda não configurou o OpenRouter."""
    return (await get_secret(db, user.id, OPENROUTER_KEY)) or "x"


def _memory_filter(ctx: ApiContext, end_user: str | None) -> dict[str, Any]:
    """Traduz o modo de memória da chave nos filtros de escopo do mem0.

    Uma chave em modo `key` só enxerga o que ela mesma gravou; em `end_user`, só o
    daquele usuário final. Sem isso, a rota de listagem seria uma porta lateral
    para ler a memória inteira da conta com uma chave restrita.
    """
    mode = keys_service.memory_mode(ctx.key)
    if mode in ("none", "request"):
        raise ApiError(
            "Esta chave está configurada sem memória persistente. Mude o modo de "
            "memória da chave para usar estes endpoints.",
            status=409, code="memory_disabled",
        )
    if mode == "persistent":
        return {"scope": "global"}
    if mode == "shared":
        return {"agent_id": "api:shared"}
    if mode == "key":
        return {"agent_id": f"api:{ctx.key.id}"}
    if not end_user:
        raise ApiError(
            "Esta chave usa memória por usuário final: informe o parâmetro 'user'.",
            code="end_user_required",
        )
    return {"chat_id": f"api:{ctx.key.id}:{end_user}"}


# --------------------------------------------------------------------------- #
# Memória
# --------------------------------------------------------------------------- #

class MemoryIn(BaseModel):
    text: str
    user: str | None = None


@router.get("/memories")
async def list_memories(
    q: str = "",
    limit: int = Query(100, ge=1, le=500),
    user: str | None = None,
    ctx: ApiContext = Depends(scoped("memory:read")),
):
    flt = _memory_filter(ctx, user)
    key = await _mem_key(ctx.db, ctx.user)
    rows = await run_in_threadpool(
        lambda: mem0_service.list_memories(
            key, str(ctx.user.id), query=q, limit=limit, **flt
        )
    )
    return {
        "object": "list",
        "mode": keys_service.memory_mode(ctx.key),
        "data": [
            {"id": r["id"], "text": r["text"], "scope": r["scope"],
             "created_at": r["created_at"], "updated_at": r["updated_at"]}
            for r in rows
        ],
    }


@router.post("/memories")
async def add_memory(body: MemoryIn, ctx: ApiContext = Depends(scoped("memory:write"))):
    text = (body.text or "").strip()
    if not text:
        raise ApiError("O campo 'text' é obrigatório.", code="text_required")
    max_items = int((ctx.key.memory or {}).get("max_items") or 0)
    flt = _memory_filter(ctx, body.user)
    key = await _mem_key(ctx.db, ctx.user)

    if max_items:
        existing = await run_in_threadpool(
            lambda: mem0_service.list_memories(key, str(ctx.user.id), limit=max_items + 1, **flt)
        )
        if len(existing) >= max_items:
            raise ApiError(
                f"Esta chave está limitada a {max_items} memórias. Apague alguma antes.",
                status=409, code="memory_limit_reached",
            )

    scope = flt.get("scope") or ("chat" if flt.get("chat_id") else "model")
    ok = await run_in_threadpool(
        lambda: mem0_service.add_manual(
            key, str(ctx.user.id), text, scope=scope,
            chat_id=flt.get("chat_id"), agent_id=flt.get("agent_id"),
        )
    )
    return {"ok": bool(ok)}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, ctx: ApiContext = Depends(scoped("memory:write"))):
    flt = _memory_filter(ctx, None)
    key = await _mem_key(ctx.db, ctx.user)
    # confirma que a memória pertence AO ESCOPO DESTA CHAVE antes de apagar: o id do
    # mem0 é global do usuário, então sem esta checagem uma chave restrita poderia
    # apagar memória de outra aplicação só sabendo o id.
    rows = await run_in_threadpool(
        lambda: mem0_service.list_memories(key, str(ctx.user.id), limit=500, **flt)
    )
    if memory_id not in {str(r["id"]) for r in rows}:
        raise ApiError("Memória não encontrada no escopo desta chave.", status=404,
                       code="memory_not_found")
    ok = await run_in_threadpool(
        mem0_service.delete_memory, key, memory_id, str(ctx.user.id))
    return {"ok": bool(ok)}


@router.post("/memories/clear")
async def clear_memories(
    user: str | None = None, ctx: ApiContext = Depends(scoped("memory:write"))
):
    """Apaga TODA a memória do escopo desta chave."""
    flt = _memory_filter(ctx, user)
    key = await _mem_key(ctx.db, ctx.user)
    rows = await run_in_threadpool(
        lambda: mem0_service.list_memories(key, str(ctx.user.id), limit=500, **flt)
    )
    removed = 0
    for r in rows:
        if await run_in_threadpool(
                mem0_service.delete_memory, key, str(r["id"]), str(ctx.user.id)):
            removed += 1
    return {"ok": True, "deleted": removed}


@router.get("/memories/export")
async def export_memories(
    user: str | None = None, ctx: ApiContext = Depends(scoped("memory:read"))
):
    """Dump do escopo, no formato que `POST /v1/memories/import` aceita de volta."""
    flt = _memory_filter(ctx, user)
    key = await _mem_key(ctx.db, ctx.user)
    rows = await run_in_threadpool(
        lambda: mem0_service.list_memories(key, str(ctx.user.id), limit=500, **flt)
    )
    return {
        "object": "memory.export",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "mode": keys_service.memory_mode(ctx.key),
        "memories": [{"text": r["text"], "created_at": r["created_at"]} for r in rows],
    }


class MemoryImport(BaseModel):
    memories: list[dict[str, Any]] = Field(default_factory=list)
    user: str | None = None


@router.post("/memories/import")
async def import_memories(
    body: MemoryImport, ctx: ApiContext = Depends(scoped("memory:write"))
):
    flt = _memory_filter(ctx, body.user)
    key = await _mem_key(ctx.db, ctx.user)
    scope = flt.get("scope") or ("chat" if flt.get("chat_id") else "model")
    added = 0
    for item in body.memories[:500]:
        text = str((item or {}).get("text") or "").strip()
        if not text:
            continue
        ok = await run_in_threadpool(
            lambda t=text: mem0_service.add_manual(
                key, str(ctx.user.id), t, scope=scope,
                chat_id=flt.get("chat_id"), agent_id=flt.get("agent_id"),
            )
        )
        added += 1 if ok else 0
    return {"ok": True, "imported": added}


# --------------------------------------------------------------------------- #
# Arquivos (documentos da Base de Conhecimento)
# --------------------------------------------------------------------------- #

async def _owned_base(ctx: ApiContext, base_id: uuid.UUID) -> KnowledgeBase:
    base = await ctx.db.get(KnowledgeBase, base_id)
    if base is None or base.user_id != ctx.user.id:
        raise ApiError("Base de conhecimento não encontrada.", status=404,
                       code="base_not_found")
    return base


def _doc_json(d: KnowledgeDoc) -> dict[str, Any]:
    return {
        "id": str(d.id), "object": "file", "filename": d.filename,
        "bytes": d.size, "mime": d.mime, "status": d.status,
        "chunks": d.chunk_count, "base_id": str(d.base_id),
        "created_at": int(d.created_at.timestamp()) if d.created_at else None,
    }


@router.get("/files")
async def list_files(
    base_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    ctx: ApiContext = Depends(scoped("files:read")),
):
    stmt = select(KnowledgeDoc).where(KnowledgeDoc.user_id == ctx.user.id)
    if base_id:
        try:
            stmt = stmt.where(KnowledgeDoc.base_id == uuid.UUID(base_id))
        except ValueError:
            raise ApiError("base_id inválido.", code="invalid_base_id") from None
    rows = list(await ctx.db.scalars(
        stmt.order_by(desc(KnowledgeDoc.created_at)).limit(limit)
    ))
    return {"object": "list", "data": [_doc_json(d) for d in rows]}


@router.get("/files/bases")
async def list_bases(ctx: ApiContext = Depends(scoped("files:read"))):
    rows = list(await ctx.db.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.user_id == ctx.user.id)
        .order_by(KnowledgeBase.name)
    ))
    return {"object": "list", "data": [
        {"id": str(b.id), "name": b.name, "kind": getattr(b, "kind", "knowledge")}
        for b in rows
    ]}


@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),
    base_id: str = Query(..., description="Base de conhecimento de destino"),
    ctx: ApiContext = Depends(scoped("files:write")),
):
    """Sobe um documento e dispara a indexação (o `status` vira `ready` no fim)."""
    try:
        bid = uuid.UUID(base_id)
    except ValueError:
        raise ApiError("base_id inválido.", code="invalid_base_id") from None
    await _owned_base(ctx, bid)

    data = await file.read()
    if not data:
        raise ApiError("Arquivo vazio.", code="empty_file")
    if len(data) > _MAX_UPLOAD:
        raise ApiError(
            f"Arquivo acima de {_MAX_UPLOAD // (1024 * 1024)} MB.",
            status=413, code="file_too_large",
        )
    doc = KnowledgeDoc(
        base_id=bid, user_id=ctx.user.id,
        filename=(file.filename or "documento")[:255],
        mime=(file.content_type or "")[:128], size=len(data),
        status="pending", chunk_count=0, data=data,
    )
    ctx.db.add(doc)
    await ctx.db.commit()
    await ctx.db.refresh(doc)
    ingest.spawn_index(doc.id)
    return _doc_json(doc)


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, ctx: ApiContext = Depends(scoped("files:write"))):
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise ApiError("Arquivo não encontrado.", status=404, code="file_not_found") from None
    doc = await ctx.db.get(KnowledgeDoc, fid)
    if doc is None or doc.user_id != ctx.user.id:
        raise ApiError("Arquivo não encontrado.", status=404, code="file_not_found")
    await ctx.db.delete(doc)
    await ctx.db.commit()
    return {"id": file_id, "object": "file", "deleted": True}


# --------------------------------------------------------------------------- #
# Uso e conta
# --------------------------------------------------------------------------- #

@router.get("/usage")
async def usage(
    days: int = Query(30, ge=1, le=90),
    ctx: ApiContext = Depends(scoped("usage:read")),
):
    """Consumo desta chave: totais do período, série diária e quebra por modelo."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    day = func.date_trunc("day", ApiRequest.created_at)

    totals = (await ctx.db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(ApiRequest.prompt_tokens), 0),
            func.coalesce(func.sum(ApiRequest.completion_tokens), 0),
            func.coalesce(func.sum(ApiRequest.cost), 0.0),
            func.coalesce(func.avg(ApiRequest.latency_ms), 0),
            func.count().filter(ApiRequest.status >= 400),
        ).where(ApiRequest.api_key_id == ctx.key.id, ApiRequest.created_at >= since)
    )).one()

    daily = (await ctx.db.execute(
        select(day, func.count(), func.coalesce(func.sum(ApiRequest.cost), 0.0),
               func.coalesce(func.sum(ApiRequest.total_tokens), 0))
        .where(ApiRequest.api_key_id == ctx.key.id, ApiRequest.created_at >= since)
        .group_by(day).order_by(day)
    )).all()

    by_model = (await ctx.db.execute(
        select(ApiRequest.model, func.count(),
               func.coalesce(func.sum(ApiRequest.total_tokens), 0),
               func.coalesce(func.sum(ApiRequest.cost), 0.0))
        .where(ApiRequest.api_key_id == ctx.key.id, ApiRequest.created_at >= since)
        .group_by(ApiRequest.model).order_by(desc(func.sum(ApiRequest.cost)))
    )).all()

    return {
        "object": "usage",
        "period_days": days,
        "totals": {
            "requests": int(totals[0] or 0),
            "prompt_tokens": int(totals[1] or 0),
            "completion_tokens": int(totals[2] or 0),
            "cost": round(float(totals[3] or 0.0), 6),
            "avg_latency_ms": int(totals[4] or 0),
            "errors": int(totals[5] or 0),
        },
        "daily": [
            {"date": d.date().isoformat(), "requests": int(c or 0),
             "cost": round(float(cost or 0.0), 6), "tokens": int(tok or 0)}
            for d, c, cost, tok in daily
        ],
        "by_model": [
            {"model": m or "", "requests": int(c or 0), "tokens": int(t or 0),
             "cost": round(float(cost or 0.0), 6)}
            for m, c, t, cost in by_model
        ],
    }


@router.get("/account")
async def account(ctx: ApiContext = Depends(scoped("usage:read"))):
    """Situação da conta e da chave: limites, quanto já foi consumido e o que sobra."""
    win = await limits.usage_window(ctx.db, ctx.key)
    budget = await budget_state(ctx.db, ctx.user)
    lim = ctx.key.limits or {}

    def _left(used: int | float, cap: Any) -> int | float | None:
        try:
            cap = float(cap or 0)
        except (TypeError, ValueError):
            return None
        return None if cap <= 0 else max(0, round(cap - used, 6))

    return {
        "object": "account",
        "key": {
            "id": str(ctx.key.id), "name": ctx.key.name,
            "prefix": keys_service.masked(ctx.key.prefix),
            "scopes": list(ctx.key.scopes or []),
            "memory_mode": keys_service.memory_mode(ctx.key),
            "expires_at": ctx.key.expires_at.isoformat() if ctx.key.expires_at else None,
        },
        "limits": {
            "rpm": lim.get("rpm") or None,
            "rpd": lim.get("rpd") or None,
            "monthly_requests": lim.get("monthly_requests") or None,
            "tokens_in": lim.get("tokens_in") or None,
            "tokens_out": lim.get("tokens_out") or None,
            "budget_usd": lim.get("budget_usd") or None,
            "concurrency": lim.get("concurrency") or None,
        },
        "usage": {
            **win,
            "requests_inflight": limits.inflight(ctx.key),
        },
        "remaining": {
            "requests_today": _left(win["requests_today"], lim.get("rpd")),
            "requests_month": _left(win["requests_month"], lim.get("monthly_requests")),
            "tokens_in": _left(win["tokens_in_month"], lim.get("tokens_in")),
            "tokens_out": _left(win["tokens_out_month"], lim.get("tokens_out")),
            "budget_usd": _left(win["cost_month"], lim.get("budget_usd")),
        },
        # orçamento da CONTA (Configurações → Conta): vale para tudo, inclusive API
        "account_budget": budget,
    }
