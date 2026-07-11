"""Base de Conhecimento (RAG): CRUD de bases, upload/indexação de documentos e
download assinado do original.

Espelha `memory_routes` (bancos) + o upload do `admin_routes`. A indexação roda em
background (`asyncio.create_task(ingest.index_doc)`), então o upload responde na hora
e o status do doc caminha pending → indexing → ready/error.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .knowledge import ingest
from .knowledge.links import sign_doc_url, verify_doc_token
from .models import KnowledgeBase, KnowledgeChunk, KnowledgeDoc, User

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_MAX_BYTES = 25 * 1024 * 1024  # 25 MB por arquivo
# mantém referência das tasks de indexação em voo (evita GC prematuro)
_TASKS: set[asyncio.Task] = set()


def _spawn_index(doc_id: uuid.UUID) -> None:
    t = asyncio.create_task(ingest.index_doc(doc_id))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class BaseIn(BaseModel):
    name: str
    description: str = ""


class BaseOut(BaseModel):
    id: str
    name: str
    description: str
    doc_count: int = 0
    chunk_count: int = 0


class DocOut(BaseModel):
    id: str
    base_id: str
    filename: str
    mime: str
    size: int
    status: str
    error: str | None = None
    chunk_count: int
    created_at: str | None = None


def _doc_out(d: KnowledgeDoc) -> DocOut:
    return DocOut(
        id=str(d.id), base_id=str(d.base_id), filename=d.filename, mime=d.mime,
        size=d.size, status=d.status, error=d.error, chunk_count=d.chunk_count,
        created_at=d.created_at.isoformat() if d.created_at else None,
    )


async def _owned_base(db: AsyncSession, user: User, base_id: uuid.UUID) -> KnowledgeBase:
    b = await db.get(KnowledgeBase, base_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Base não encontrada")
    return b


async def _owned_doc(db: AsyncSession, user: User, doc_id: uuid.UUID) -> KnowledgeDoc:
    d = await db.get(KnowledgeDoc, doc_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    return d


# --------------------------------------------------------------------------- #
# Bases
# --------------------------------------------------------------------------- #
@router.get("/bases", response_model=list[BaseOut])
async def list_bases(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    bases = list(await db.scalars(
        select(KnowledgeBase).where(KnowledgeBase.user_id == user.id).order_by(KnowledgeBase.created_at)
    ))
    # contagens (docs + soma de chunks) por base, numa query
    rows = (await db.execute(
        select(
            KnowledgeDoc.base_id,
            func.count(KnowledgeDoc.id),
            func.coalesce(func.sum(KnowledgeDoc.chunk_count), 0),
        ).where(KnowledgeDoc.user_id == user.id).group_by(KnowledgeDoc.base_id)
    )).all()
    counts = {r[0]: (r[1], r[2]) for r in rows}
    return [
        BaseOut(
            id=str(b.id), name=b.name, description=b.description or "",
            doc_count=counts.get(b.id, (0, 0))[0], chunk_count=counts.get(b.id, (0, 0))[1],
        )
        for b in bases
    ]


@router.post("/bases", response_model=BaseOut)
async def create_base(body: BaseIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = KnowledgeBase(
        user_id=user.id, name=(body.name or "Base").strip()[:120],
        description=(body.description or "").strip(),
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return BaseOut(id=str(b.id), name=b.name, description=b.description or "")


@router.patch("/bases/{base_id}", response_model=BaseOut)
async def update_base(
    base_id: uuid.UUID, body: BaseIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    b = await _owned_base(db, user, base_id)
    b.name = (body.name or "Base").strip()[:120]
    b.description = (body.description or "").strip()
    await db.commit()
    return BaseOut(id=str(b.id), name=b.name, description=b.description or "")


@router.delete("/bases/{base_id}")
async def delete_base(base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = await _owned_base(db, user, base_id)
    await db.delete(b)  # cascade apaga docs + chunks
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Documentos
# --------------------------------------------------------------------------- #
@router.get("/bases/{base_id}/docs", response_model=list[DocOut])
async def list_docs(base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await _owned_base(db, user, base_id)
    docs = list(await db.scalars(
        select(KnowledgeDoc).where(KnowledgeDoc.base_id == base_id).order_by(KnowledgeDoc.created_at)
    ))
    return [_doc_out(d) for d in docs]


@router.post("/bases/{base_id}/docs", response_model=list[DocOut])
async def upload_docs(
    base_id: uuid.UUID, files: list[UploadFile],
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _owned_base(db, user, base_id)
    created: list[KnowledgeDoc] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        if len(data) > _MAX_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{f.filename}: arquivo acima de 25 MB")
        d = KnowledgeDoc(
            base_id=base_id, user_id=user.id,
            filename=(f.filename or "documento")[:255],
            mime=(f.content_type or "")[:128], size=len(data),
            status="pending", chunk_count=0, data=data,
        )
        db.add(d)
        created.append(d)
    await db.commit()
    for d in created:
        await db.refresh(d)
        _spawn_index(d.id)
    return [_doc_out(d) for d in created]


@router.post("/docs/{doc_id}/reindex", response_model=DocOut)
async def reindex_doc(doc_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    d = await _owned_doc(db, user, doc_id)
    if not d.data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Documento sem bytes originais para reindexar")
    d.status = "pending"
    d.error = None
    await db.commit()
    await db.refresh(d)
    _spawn_index(d.id)
    return _doc_out(d)


@router.delete("/docs/{doc_id}")
async def delete_doc(doc_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    d = await _owned_doc(db, user, doc_id)
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id))
    await db.delete(d)
    await db.commit()
    return {"ok": True}


@router.get("/docs/{doc_id}/raw")
async def get_doc_raw(doc_id: uuid.UUID, t: str = "", db: AsyncSession = Depends(get_db)):
    """Baixa o arquivo original de uma fonte citada. Autoriza pelo token assinado
    `t` (URL-capacidade), então funciona num link direto sem cookie."""
    if not verify_doc_token(str(doc_id), t):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")
    d = await db.get(KnowledgeDoc, doc_id)
    if d is None or not d.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    return Response(
        content=bytes(d.data),
        media_type=d.mime or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{d.filename or "documento"}"'},
    )
