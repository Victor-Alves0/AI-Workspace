"""Modelos personalizados (estilo "Models" do OpenWebUI): CRUD."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from .auth.deps import require_approved
from .db import get_db
from .models import ModelConfig, User

router = APIRouter(prefix="/models", tags=["models"])


class ModelIn(BaseModel):
    base_model: str
    name: str
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = None
    avatar_url: str | None = None
    system_prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # flags booleanas + objetos aninhados (memory/knowledge/brain/token_warn…)
    capabilities: dict[str, Any] = Field(default_factory=dict)
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
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = None
    avatar_url: str | None = None
    system_prompt: str | None = None
    params: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
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
    capabilities: dict[str, Any]
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


def _clean_slug(value: str | None) -> str | None:
    """Normaliza o ID público sem transformar uma string vazia em colisão."""
    cleaned = (value or "").strip()
    return cleaned or None


async def _ensure_slug_available(
    db: AsyncSession, user: User, slug: str | None, *, except_id: uuid.UUID | None = None
) -> None:
    """Impede ambiguidade antes do commit e devolve um erro útil à UI."""
    if slug is None:
        return
    found = await db.scalar(
        select(ModelConfig.id).where(
            ModelConfig.user_id == user.id,
            ModelConfig.slug == slug,
        )
    )
    if found is not None and found != except_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Já existe um modelo com o ID '@{slug}'. Escolha outro ID.",
        )


async def _commit_model(db: AsyncSession, *, slug: str | None) -> None:
    """A constraint cobre duas gravações concorrentes após a verificação acima."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_model_config_user_slug" in str(exc.orig):
            shown = f" '@{slug}'" if slug else ""
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Já existe um modelo com o ID{shown}. Escolha outro ID.",
            ) from exc
        raise


@router.post("", response_model=ModelOut)
async def create_model(
    body: ModelIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    data = body.model_dump()
    data["slug"] = _clean_slug(data.get("slug"))
    await _ensure_slug_available(db, user, data["slug"])
    mc = ModelConfig(user_id=user.id, **data)
    db.add(mc)
    await _commit_model(db, slug=data["slug"])
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
    data = body.model_dump(exclude_unset=True)
    if "slug" in data:
        data["slug"] = _clean_slug(data["slug"])
        await _ensure_slug_available(db, user, data["slug"], except_id=mc.id)
    for field, value in data.items():
        setattr(mc, field, value)
    await _commit_model(db, slug=mc.slug)
    await db.refresh(mc)
    return mc


@router.delete("/{model_id}")
async def delete_model(
    model_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    mc = await _owned(db, model_id, user)
    await _clear_model_references(db, user, str(model_id))
    await db.delete(mc)
    await db.commit()
    return {"ok": True}


async def _clear_model_references(db: AsyncSession, user: User, model_id: str) -> None:
    """Remove as referências "custom:<id>" ao modelo que está sendo apagado.

    Sem isto elas ficam PENDURADAS: o seletor não acha o ModelConfig e passava a exibir
    (e enviar) a string crua "custom:<uuid>" como se fosse o nome de um modelo — foi o
    bug do rótulo `custom:b15bfddd-…` marcado como "Modelo padrão". Cobre o padrão do
    usuário, o padrão dos projetos do Codespace e as listas de fixados/favoritos.
    Os chats não precisam: `model_config_id` é FK (o banco resolve)."""
    from .models import CodespaceProject

    ref = f"custom:{model_id}"
    if (user.default_model or "") == ref:
        user.default_model = None
    profile = dict(user.profile or {})
    changed = False
    for field in ("pinned_models", "favorite_models"):
        vals = profile.get(field)
        if isinstance(vals, list) and ref in vals:
            profile[field] = [v for v in vals if v != ref]
            changed = True
    if changed:
        user.profile = profile  # reatribui: JSONB não detecta mutação in-place
    projs = await db.scalars(
        select(CodespaceProject).where(
            CodespaceProject.user_id == user.id, CodespaceProject.default_model == ref
        )
    )
    for p in projs:
        if (p.default_model or "") == ref:  # confere de novo: não depende só do WHERE
            p.default_model = None
