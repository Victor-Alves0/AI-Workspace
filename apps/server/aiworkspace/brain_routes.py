"""Second brain: o que o explorador de conhecimento não cobre — grafo de notas
[[interligadas]] e resolução de wikilink → nota.

CRUD de notas/pastas/texto/reindex/download é o `knowledge_routes` (cérebros são
`knowledge_bases` com kind="brain"). Aqui só o específico de notas: o grafo é
parseado on-demand de todos os `.md` do cérebro (escala pessoal — sem tabela de
links a sincronizar).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .knowledge import brain as brain_service
from .models import KnowledgeBase, User

router = APIRouter(prefix="/brain", tags=["brain"])


async def _owned_brain(db: AsyncSession, user: User, base_id: uuid.UUID) -> KnowledgeBase:
    b = await db.get(KnowledgeBase, base_id)
    if b is None or b.user_id != user.id or b.kind != "brain":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cérebro não encontrado")
    return b


@router.get("/bases/{base_id}/graph")
async def get_graph(
    base_id: uuid.UUID,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Grafo do cérebro: nós = notas (+ ghost p/ [[links]] sem alvo), arestas =
    wikilinks resolvidos."""
    await _owned_brain(db, user, base_id)
    docs = await brain_service.load_brain_docs([base_id], user.id)
    return brain_service.build_graph(docs)


@router.get("/bases/{base_id}/resolve")
async def resolve_title(
    base_id: uuid.UUID, title: str = "",
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Wikilink → nota: devolve o doc cujo nome/título casa com `title`
    (navegação por [[link]] na UI). 404 = nota ainda não existe."""
    await _owned_brain(db, user, base_id)
    note = await brain_service.resolve_note(user.id, [base_id], title)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nota não encontrada")
    return {"doc_id": note["id"], "filename": note["filename"], "title": note["title"]}
