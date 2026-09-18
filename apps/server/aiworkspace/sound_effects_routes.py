"""Rota que o botão de som chama: gera (ou reaproveita) o efeito e devolve a URL."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from . import sound_effects
from .auth.deps import require_approved
from .db import get_db
from .models import User

router = APIRouter(prefix="/sfx", tags=["sound-effects"])


class SoundRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=400)


@router.post("")
async def play_sound(
    body: SoundRequest,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await sound_effects.get_or_generate(db, user.id, body.prompt)
    except sound_effects.SoundUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RuntimeError as exc:           # a ElevenLabs recusou ou caiu
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@router.get("/available")
async def sound_available(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """O botão aparece desabilitado (com a dica) quando não há ElevenLabs ligada."""
    return {"available": await sound_effects.available(db, user.id)}
