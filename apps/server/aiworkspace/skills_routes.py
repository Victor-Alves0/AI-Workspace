"""Aba Skills: CRUD de skills do usuário.

Skills são documentos (sem execução de código) — disponíveis a qualquer usuário
aprovado. O modelo vê nome+descrição e carrega o conteúdo completo sob demanda via
`view_skill` (lazy loading). No chat, `$slug` invoca uma skill para o turno.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import Skill, SkillProposal, User

router = APIRouter(prefix="/skills", tags=["skills"])

_SLUG = r"^[a-z0-9][a-z0-9_]*$"


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower().strip()).strip("_")
    return s or "skill"


class SkillIn(BaseModel):
    slug: str = Field(pattern=_SLUG, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=200_000)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class SkillUpdate(BaseModel):
    slug: str | None = Field(default=None, pattern=_SLUG, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=200_000)
    tags: list[str] | None = None
    enabled: bool | None = None


class SkillOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    content: str
    tags: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = t.strip().lower()[:40]
        if t and t not in out:
            out.append(t)
    return out[:10]


@router.get("", response_model=list[SkillOut])
async def list_skills(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Skill).where(Skill.user_id == user.id).order_by(Skill.name)
    )
    return list(rows)


@router.post("", response_model=SkillOut)
async def create_skill(
    body: SkillIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    data = body.model_dump()
    data["slug"] = data["slug"] or _slugify(data["name"])
    data["tags"] = _clean_tags(data["tags"])
    skill = Skill(user_id=user.id, **data)
    db.add(skill)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe uma skill com esse identificador")
    await db.refresh(skill)
    return skill


async def _owned(db: AsyncSession, skill_id: uuid.UUID, user: User) -> Skill:
    s = await db.get(Skill, skill_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill não encontrada")
    return s


@router.patch("/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    s = await _owned(db, skill_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "tags" and value is not None:
            value = _clean_tags(value)
        setattr(s, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe uma skill com esse identificador")
    await db.refresh(s)
    return s


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    s = await _owned(db, skill_id, user)
    await db.delete(s)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Propostas de skill do Aprendizado Proativo (Curator): fila para aprovação.
# --------------------------------------------------------------------------- #
class ProposalOut(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID | None
    name: str
    slug: str
    description: str
    content: str
    tags: list[str]
    source: str
    created_at: datetime

    class Config:
        from_attributes = True


async def _owned_proposal(db: AsyncSession, pid: uuid.UUID, user: User) -> SkillProposal:
    p = await db.get(SkillProposal, pid)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposta não encontrada")
    return p


@router.get("/proposals", response_model=list[ProposalOut])
async def list_proposals(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(SkillProposal)
        .where(SkillProposal.user_id == user.id, SkillProposal.status == "pending")
        .order_by(SkillProposal.created_at.desc())
    )
    return list(rows)


async def _unique_slug(db: AsyncSession, user: User, base: str) -> str:
    base = _slugify(base)
    existing = set(
        await db.scalars(select(Skill.slug).where(Skill.user_id == user.id))
    )
    if base not in existing:
        return base
    for n in range(2, 100):
        cand = f"{base}_{n}"[:64]
        if cand not in existing:
            return cand
    return f"{base}_{uuid.uuid4().hex[:6]}"[:64]


@router.post("/proposals/{proposal_id}/approve", response_model=SkillOut)
async def approve_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Aprova uma proposta → cria a Skill (slug único) e marca a proposta como aprovada."""
    p = await _owned_proposal(db, proposal_id, user)
    slug = await _unique_slug(db, user, p.slug or p.name)
    skill = Skill(
        user_id=user.id, slug=slug, name=p.name, description=p.description,
        content=p.content, tags=_clean_tags(list(p.tags or [])), enabled=True,
    )
    db.add(skill)
    p.status = "approved"
    await db.commit()
    await db.refresh(skill)
    return skill


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned_proposal(db, proposal_id, user)
    p.status = "dismissed"
    await db.commit()
    return {"ok": True}
