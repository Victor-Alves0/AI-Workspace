"""Rotas dos Artefatos: listagem por chat, edição em tempo real, histórico de
versões (visualizar/restaurar), duplicar, renomear e compartilhamento por link
assinado (URL-capacidade, como as imagens geradas)."""

from __future__ import annotations

import time
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .config import get_settings
from .db import get_db
from .models import Artifact, ArtifactVersion, Chat, User

router = APIRouter(tags=["artifacts"])

_SHARE_MIME = {
    "html": "text/html; charset=utf-8",
    "svg": "image/svg+xml",
    "json": "application/json; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


def _serialize(a: Artifact) -> dict:
    return {
        "id": str(a.id),
        "chat_id": str(a.chat_id),
        "identifier": a.identifier,
        "title": a.title,
        "kind": a.kind,
        "language": a.language,
        "content": a.content,
        "version": a.version,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def _owned(db: AsyncSession, artifact_id: uuid.UUID, user: User) -> Artifact:
    a = await db.get(Artifact, artifact_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefato não encontrado")
    return a


@router.get("/chats/{chat_id}/artifacts")
async def list_artifacts(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat não encontrado")
    rows = await db.scalars(
        select(Artifact).where(Artifact.chat_id == chat_id).order_by(Artifact.created_at)
    )
    return [_serialize(a) for a in rows]


class ArtifactUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    content: str | None = None


@router.patch("/artifacts/{artifact_id}")
async def update_artifact(
    artifact_id: uuid.UUID,
    body: ArtifactUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Edição em tempo real / renomear. Mudança de conteúdo gera versão "user"."""
    a = await _owned(db, artifact_id, user)
    if body.title is not None and body.title.strip():
        a.title = body.title.strip()
    if body.content is not None and body.content != a.content:
        a.content = body.content
        a.version += 1
        db.add(ArtifactVersion(artifact_id=a.id, version=a.version, content=body.content, label="user"))
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, artifact_id, user)
    await db.delete(a)
    await db.commit()


@router.get("/artifacts/{artifact_id}/versions")
async def list_versions(
    artifact_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, artifact_id, user)
    rows = await db.scalars(
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_id == a.id)
        .order_by(ArtifactVersion.version.desc())
    )
    return [
        {"version": v.version, "label": v.label, "size": len(v.content or ""),
         "created_at": v.created_at.isoformat() if v.created_at else None}
        for v in rows
    ]


@router.get("/artifacts/{artifact_id}/versions/{version}")
async def get_version(
    artifact_id: uuid.UUID,
    version: int,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, artifact_id, user)
    v = await db.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == a.id, ArtifactVersion.version == version
        )
    )
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versão não encontrada")
    return {"version": v.version, "label": v.label, "content": v.content}


class RestoreIn(BaseModel):
    version: int


@router.post("/artifacts/{artifact_id}/restore")
async def restore_version(
    artifact_id: uuid.UUID,
    body: RestoreIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Restaurar vira uma versão NOVA (label "restore") — nada do histórico se perde."""
    a = await _owned(db, artifact_id, user)
    v = await db.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == a.id, ArtifactVersion.version == body.version
        )
    )
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Versão não encontrada")
    a.content = v.content
    a.version += 1
    db.add(ArtifactVersion(artifact_id=a.id, version=a.version, content=v.content, label="restore"))
    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.post("/artifacts/{artifact_id}/duplicate")
async def duplicate_artifact(
    artifact_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, artifact_id, user)
    ident = f"{a.identifier[:70]}-copia"
    # garante identifier único no chat
    n = 1
    base = ident
    while await db.scalar(
        select(Artifact.id).where(Artifact.chat_id == a.chat_id, Artifact.identifier == ident)
    ):
        n += 1
        ident = f"{base}-{n}"
    dup = Artifact(
        chat_id=a.chat_id, user_id=a.user_id, identifier=ident,
        title=f"{a.title} (cópia)"[:255], kind=a.kind, language=a.language,
        content=a.content, version=1,
    )
    db.add(dup)
    await db.flush()
    db.add(ArtifactVersion(artifact_id=dup.id, version=1, content=dup.content, label="user"))
    await db.commit()
    await db.refresh(dup)
    return _serialize(dup)


# --------------------------------------------------------------------------- #
# Compartilhar (link assinado, público)
# --------------------------------------------------------------------------- #
@router.post("/artifacts/{artifact_id}/share")
async def share_artifact(
    artifact_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, artifact_id, user)
    now = int(time.time())
    tok = jwt.encode(
        {"art": str(a.id), "iat": now, "exp": now + 30 * 86400},
        get_settings().app_secret,
        algorithm="HS256",
    )
    return {"path": f"/artifacts/shared/{tok}", "expires_days": 30}


@router.get("/artifacts/shared/{token}")
async def get_shared(token: str, db: AsyncSession = Depends(get_db)):
    try:
        data = jwt.decode(token, get_settings().app_secret, algorithms=["HS256"])
        aid = uuid.UUID(str(data.get("art")))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Link inválido ou expirado")
    a = await db.get(Artifact, aid)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefato não encontrado")
    mime = _SHARE_MIME.get(a.kind, "text/plain; charset=utf-8")
    return Response(content=a.content or "", media_type=mime)
