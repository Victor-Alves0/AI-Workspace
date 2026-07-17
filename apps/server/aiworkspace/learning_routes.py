"""Configuração do Aprendizado Proativo (Curator).

Toggle global por-usuário (em Configurações → Controle de Dados). Guarda em
`user.profile["learning"]`, mesmo padrão de `memory_routes` (`/memory/settings`).
Vem DESLIGADO (opt-in): custa tokens (revisão em background a cada N turnos).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import User

router = APIRouter(prefix="/learning", tags=["learning"])

DEFAULT_LEARNING = {"enabled": False, "interval": 10, "model": ""}


class LearningSettings(BaseModel):
    enabled: bool = False
    # a cada quantos turnos revisar (piso 4 p/ não revisar cedo/caro demais)
    interval: int = Field(default=10, ge=4, le=100)
    # modelo da revisão (vazio = usa o modelo do turno)
    model: str = ""


@router.get("/settings", response_model=LearningSettings)
async def get_learning_settings(user: User = Depends(require_approved)):
    cfg = {**DEFAULT_LEARNING, **((user.profile or {}).get("learning") or {})}
    return LearningSettings(**cfg)


@router.put("/settings", response_model=LearningSettings)
async def put_learning_settings(
    body: LearningSettings,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    prof = dict(user.profile or {})
    prof["learning"] = body.model_dump()
    user.profile = prof
    await db.commit()
    return body
