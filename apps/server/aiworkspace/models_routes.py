"""Modelos personalizados (estilo "Models" do OpenWebUI): CRUD."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import ModelConfig, User

router = APIRouter(prefix="/models", tags=["models"])


class ModelIn(BaseModel):
    base_model: str
    name: str
    slug: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    system_prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    filter_config: dict[str, Any] = Field(default_factory=dict)
    tools_enabled: bool = False
    tool_ids: list[str] = Field(default_factory=list)
    code_mode: bool = False
    sift_config: dict[str, Any] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list)
    prompt_suggestions: list[str] = Field(default_factory=list)
    tts_voice: str | None = None
    enabled: bool = True


class ModelUpdate(BaseModel):
    base_model: str | None = None
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    system_prompt: str | None = None
    params: dict[str, Any] | None = None
    capabilities: dict[str, bool] | None = None
    filter_config: dict[str, Any] | None = None
    tools_enabled: bool | None = None
    tool_ids: list[str] | None = None
    code_mode: bool | None = None
    sift_config: dict[str, Any] | None = None
    skill_ids: list[str] | None = None
    prompt_suggestions: list[str] | None = None
    tts_voice: str | None = None
    enabled: bool | None = None


class ModelOut(BaseModel):
    id: uuid.UUID
    base_model: str
    name: str
    slug: str | None = None
    description: str | None
    avatar_url: str | None
    system_prompt: str | None
    params: dict[str, Any]
    capabilities: dict[str, bool]
    filter_config: dict[str, Any] = Field(default_factory=dict)
    tools_enabled: bool
    tool_ids: list[str]
    code_mode: bool
    sift_config: dict[str, Any] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list)
    prompt_suggestions: list[str]
    tts_voice: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[ModelOut])
async def list_models(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(ModelConfig).where(ModelConfig.user_id == user.id).order_by(ModelConfig.name)
    )
    return list(rows)


@router.post("", response_model=ModelOut)
async def create_model(
    body: ModelIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    mc = ModelConfig(user_id=user.id, **body.model_dump())
    db.add(mc)
    await db.commit()
    await db.refresh(mc)
    return mc


async def _owned(db: AsyncSession, model_id: uuid.UUID, user: User) -> ModelConfig:
    mc = await db.get(ModelConfig, model_id)
    if mc is None or mc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modelo não encontrado")
    return mc


@router.patch("/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: uuid.UUID,
    body: ModelUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    mc = await _owned(db, model_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(mc, field, value)
    await db.commit()
    await db.refresh(mc)
    return mc


@router.delete("/{model_id}")
async def delete_model(
    model_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    mc = await _owned(db, model_id, user)
    await db.delete(mc)
    await db.commit()
    return {"ok": True}
