"""Controlador de Memória (mem0): visualizar, editar e excluir o que a IA lembra.

Três escopos (ver `memory/mem0_service`): **global** (compartilhado por tudo),
**model** (por modelo/agente) e **chat** (por conversa). Aqui ficam a listagem
por escopo, edição/adição/exclusão manual e as configurações do usuário
(memória ligada + padrões de leitura/escrita para novos chats).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .memory import mem0_service
from .models import Chat, Folder, MemoryBank, ModelConfig, User
from .secrets_service import OPENROUTER_KEY, get_secret

router = APIRouter(prefix="/memory", tags=["memory"])

# padrão de memória (novos chats): OPT-IN — desligada até o usuário ativar (nas
# Configurações de Memória, por modelo/agente, ou por chat). Sobrescrevível nos 3 níveis.
DEFAULT_MEMORY = {
    "enabled": False,
    "write": "global",
    "read": {"global": True, "model": True, "chat": True},
    "review": False,  # revisar antes de salvar: novas memórias entram como pendentes
}


async def _key(db: AsyncSession, user: User) -> str:
    # ops de gerência não chamam o LLM; a chave real é usada se existir
    return (await get_secret(db, user.id, OPENROUTER_KEY)) or "x"


class MemoryOut(BaseModel):
    id: str
    text: str
    scope: str
    disabled: bool = False
    model_id: str | None = None
    model_name: str | None = None
    chat_id: str | None = None
    chat_title: str | None = None
    bank_id: str | None = None
    bank_name: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryIn(BaseModel):
    text: str
    scope: str = "global"  # global | model | chat | bank | project
    model_id: str | None = None
    chat_id: str | None = None
    bank_id: str | None = None
    project_id: str | None = None


class MemoryPatch(BaseModel):
    text: str


class BulkIn(BaseModel):
    action: str  # delete | disable | enable
    ids: list[str]


class MemorySettings(BaseModel):
    enabled: bool = False
    write: str = "global"
    read: dict[str, bool] = DEFAULT_MEMORY["read"]
    review: bool = False


async def _model_names(db: AsyncSession, user: User, ids: list[str]) -> dict[str, str]:
    """Resolve agent_ids → nome exibível. UUID = ModelConfig.name; `base:<m>` = o modelo."""
    out: dict[str, str] = {}
    uuids: list[uuid.UUID] = []
    for i in ids:
        if i.startswith("base:"):
            out[i] = i[5:]
        else:
            try:
                uuids.append(uuid.UUID(i))
            except ValueError:
                out[i] = i
    if uuids:
        rows = await db.scalars(
            select(ModelConfig).where(ModelConfig.user_id == user.id, ModelConfig.id.in_(uuids))
        )
        for mc in rows:
            out[str(mc.id)] = mc.name
    for i in ids:
        out.setdefault(i, "Modelo removido")
    return out


async def _chat_titles(db: AsyncSession, user: User, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    uuids: list[uuid.UUID] = []
    for i in ids:
        try:
            uuids.append(uuid.UUID(i))
        except ValueError:
            out[i] = i
    if uuids:
        rows = await db.scalars(
            select(Chat).where(Chat.user_id == user.id, Chat.id.in_(uuids))
        )
        for c in rows:
            out[str(c.id)] = c.title
    for i in ids:
        out.setdefault(i, "Chat removido")
    return out


async def _bank_names(db: AsyncSession, user: User, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    uuids: list[uuid.UUID] = []
    for i in ids:
        try:
            uuids.append(uuid.UUID(i))
        except ValueError:
            out[i] = i
    if uuids:
        rows = await db.scalars(
            select(MemoryBank).where(MemoryBank.user_id == user.id, MemoryBank.id.in_(uuids))
        )
        for b in rows:
            out[str(b.id)] = b.name or "Banco"
    for i in ids:
        out.setdefault(i, "Banco removido")
    return out


async def _folder_names(db: AsyncSession, user: User, ids: list[str]) -> dict[str, str]:
    """Projeto = pasta (folder_id). Resolve id → nome da pasta."""
    out: dict[str, str] = {}
    uuids: list[uuid.UUID] = []
    for i in ids:
        try:
            uuids.append(uuid.UUID(i))
        except ValueError:
            out[i] = i
    if uuids:
        rows = await db.scalars(
            select(Folder).where(Folder.user_id == user.id, Folder.id.in_(uuids))
        )
        for f in rows:
            out[str(f.id)] = f.name or "Projeto"
    for i in ids:
        out.setdefault(i, "Projeto removido")
    return out


# --------------------------------------------------------------------------- #
# Bancos de memória (coleções compartilháveis entre modelos)
# --------------------------------------------------------------------------- #
class BankIn(BaseModel):
    name: str
    description: str = ""


class BankOut(BaseModel):
    id: str
    name: str
    description: str
    count: int = 0


@router.get("/banks", response_model=list[BankOut])
async def list_banks(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(
        select(MemoryBank).where(MemoryBank.user_id == user.id).order_by(MemoryBank.created_at)
    ))
    key = await _key(db, user)
    counts = await run_in_threadpool(mem0_service.bank_counts, key, str(user.id))
    return [
        BankOut(id=str(b.id), name=b.name, description=b.description or "", count=counts.get(str(b.id), 0))
        for b in rows
    ]


@router.post("/banks", response_model=BankOut)
async def create_bank(
    body: BankIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    b = MemoryBank(user_id=user.id, name=(body.name or "Banco").strip()[:120], description=(body.description or "").strip())
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return BankOut(id=str(b.id), name=b.name, description=b.description or "", count=0)


@router.delete("/banks/{bank_id}")
async def delete_bank(
    bank_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    b = await db.get(MemoryBank, bank_id)
    if b is None or b.user_id != user.id:
        return {"ok": True}
    key = await _key(db, user)
    # apaga as memórias do banco no mem0 (agent_id "bank:<id>") e o registro
    await run_in_threadpool(
        lambda: mem0_service.delete_scope(key, str(user.id), scope="bank", agent_id=f"bank:{bank_id}")
    )
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@router.get("/settings", response_model=MemorySettings)
async def get_settings_(user: User = Depends(require_approved)):
    cfg = {**DEFAULT_MEMORY, **((user.profile or {}).get("memory") or {})}
    return MemorySettings(**cfg)


@router.put("/settings", response_model=MemorySettings)
async def put_settings(
    body: MemorySettings,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    prof = dict(user.profile or {})
    prof["memory"] = body.model_dump()
    user.profile = prof
    await db.commit()
    return body


@router.get("/scopes")
async def scopes(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    """Resumo p/ os seletores: contagem global + listas de modelos/chats com memória."""
    key = await _key(db, user)
    summary = await run_in_threadpool(mem0_service.scope_summary, key, str(user.id))
    mnames = await _model_names(db, user, list(summary["models"].keys()))
    ctitles = await _chat_titles(db, user, list(summary["chats"].keys()))
    bnames = await _bank_names(db, user, list(summary.get("banks", {}).keys()))
    fnames = await _folder_names(db, user, list(summary.get("projects", {}).keys()))
    return {
        "global": summary["global"],
        "total": summary["total"],
        "models": [{"id": k, "name": mnames.get(k, k), "count": v} for k, v in summary["models"].items()],
        "chats": [{"id": k, "title": ctitles.get(k, k), "count": v} for k, v in summary["chats"].items()],
        "banks": [{"id": k, "name": bnames.get(k, k), "count": v} for k, v in summary.get("banks", {}).items()],
        "projects": [{"id": k, "name": fnames.get(k, k), "count": v} for k, v in summary.get("projects", {}).items()],
    }


@router.get("/pending", response_model=list[MemoryOut])
async def list_pending(
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Memórias aguardando revisão (quando 'Revisar antes de salvar' está ligado)."""
    key = await _key(db, user)
    rows = await run_in_threadpool(lambda: mem0_service.list_memories(key, str(user.id)))
    rows = [r for r in rows if r.get("pending")]
    mids = {r["model_id"] for r in rows if r["model_id"]}
    cids = {r["chat_id"] for r in rows if r["chat_id"]}
    mnames = await _model_names(db, user, list(mids))
    ctitles = await _chat_titles(db, user, list(cids))
    return [
        MemoryOut(
            id=r["id"], text=r["text"], scope=r["scope"], disabled=r.get("disabled", False),
            model_id=r["model_id"], chat_id=r["chat_id"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            model_name=mnames.get(r["model_id"]) if r["model_id"] else None,
            chat_title=ctitles.get(r["chat_id"]) if r["chat_id"] else None,
        )
        for r in rows
    ]


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    scope: str | None = None,
    model_id: str | None = None,
    chat_id: str | None = None,
    bank_id: str | None = None,
    project_id: str | None = None,
    q: str = "",
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    key = await _key(db, user)
    rows = await run_in_threadpool(
        lambda: mem0_service.list_memories(
            key, str(user.id), scope=scope, chat_id=chat_id, agent_id=model_id,
            bank_id=bank_id, project_id=project_id, query=q,
        )
    )
    mids = {r["model_id"] for r in rows if r["model_id"]}
    cids = {r["chat_id"] for r in rows if r["chat_id"]}
    bids = {r.get("bank_id") for r in rows if r.get("bank_id")}
    pids = {r.get("project_id") for r in rows if r.get("project_id")}
    mnames = await _model_names(db, user, list(mids))
    ctitles = await _chat_titles(db, user, list(cids))
    bnames = await _bank_names(db, user, list(bids))
    fnames = await _folder_names(db, user, list(pids))
    return [
        MemoryOut(
            id=r["id"], text=r["text"], scope=r["scope"], disabled=r.get("disabled", False),
            model_id=r["model_id"], chat_id=r["chat_id"], bank_id=r.get("bank_id"),
            project_id=r.get("project_id"),
            created_at=r["created_at"], updated_at=r["updated_at"],
            model_name=mnames.get(r["model_id"]) if r["model_id"] else None,
            chat_title=ctitles.get(r["chat_id"]) if r["chat_id"] else None,
            bank_name=bnames.get(r["bank_id"]) if r.get("bank_id") else None,
            project_name=fnames.get(r["project_id"]) if r.get("project_id") else None,
        )
        for r in rows
    ]


@router.post("")
async def add_memory(
    body: MemoryIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    key = await _key(db, user)
    # bank/project: o agent_id da memória é o id do banco/pasta (add_manual prefixa)
    agent = (
        body.bank_id if body.scope == "bank"
        else body.project_id if body.scope == "project"
        else body.model_id
    )
    ok = await run_in_threadpool(
        lambda: mem0_service.add_manual(
            key, str(user.id), body.text, scope=body.scope,
            chat_id=body.chat_id, agent_id=agent,
        )
    )
    return {"ok": ok}


@router.put("/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryPatch,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    key = await _key(db, user)
    # str(user.id) é o ESCOPO de posse (o `key` é a chave do LLM, não autoriza nada)
    ok = await run_in_threadpool(
        mem0_service.update_memory, key, memory_id, body.text, str(user.id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memória não encontrada")
    return {"ok": ok}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    key = await _key(db, user)
    ok = await run_in_threadpool(
        mem0_service.delete_memory, key, memory_id, str(user.id))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Memória não encontrada")
    return {"ok": ok}


@router.post("/bulk")
async def bulk(
    body: BulkIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Ação em lote sobre memórias selecionadas: excluir, desativar ou ativar."""
    key = await _key(db, user)
    ids = body.ids or []
    if not ids:
        return {"ok": True, "affected": 0}
    if body.action == "delete":
        def _del() -> int:
            return sum(1 for m in ids if mem0_service.delete_memory(key, m, str(user.id)))
        n = await run_in_threadpool(_del)
    elif body.action in ("disable", "enable"):
        n = await run_in_threadpool(
            mem0_service.set_disabled, str(user.id), ids, body.action == "disable"
        )
    else:
        n = 0
    return {"ok": True, "affected": n}
