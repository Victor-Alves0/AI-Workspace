"""Aba Prompts: CRUD de prompts reutilizáveis do usuário.

Prompts são conteúdo (sem execução de código) — disponível para qualquer usuário
aprovado. No chat, `/comando` transcreve o conteúdo para o campo de mensagem.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import Prompt, User

router = APIRouter(prefix="/prompts", tags=["prompts"])

_COMMAND = r"^[a-z0-9][a-z0-9-]*$"


class PromptIn(BaseModel):
    command: str = Field(pattern=_COMMAND, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=100_000)
    enabled: bool = True


class PromptUpdate(BaseModel):
    command: str | None = Field(default=None, pattern=_COMMAND, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=100_000)
    enabled: bool | None = None


class PromptOut(BaseModel):
    id: uuid.UUID
    command: str
    title: str
    content: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[PromptOut])
async def list_prompts(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Prompt).where(Prompt.user_id == user.id).order_by(Prompt.command)
    )
    return list(rows)


@router.post("", response_model=PromptOut)
async def create_prompt(
    body: PromptIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    prompt = Prompt(user_id=user.id, **body.model_dump())
    db.add(prompt)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe um prompt com esse comando")
    await db.refresh(prompt)
    return prompt


async def _owned(db: AsyncSession, prompt_id: uuid.UUID, user: User) -> Prompt:
    p = await db.get(Prompt, prompt_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prompt não encontrado")
    return p


@router.patch("/{prompt_id}", response_model=PromptOut)
async def update_prompt(
    prompt_id: uuid.UUID,
    body: PromptUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned(db, prompt_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(p, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe um prompt com esse comando")
    await db.refresh(p)
    return p


@router.delete("/{prompt_id}")
async def delete_prompt(
    prompt_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned(db, prompt_id, user)
    await db.delete(p)
    await db.commit()
    return {"ok": True}
