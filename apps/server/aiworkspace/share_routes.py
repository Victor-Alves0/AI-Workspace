"""Leitura pública (sem auth) de um chat compartilhado.

`GET /shared/{public_id}` devolve o chat em modo read-only: título + mensagens
(papel/conteúdo/hora), sem nada sensível. Só funciona enquanto o dono mantém o
`public_id` setado (revogar = 404). Mensagens compactadas/resumo são omitidas.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import Chat

router = APIRouter(tags=["share"])


class SharedMessage(BaseModel):
    role: str
    content: str
    created_at: datetime


class SharedChat(BaseModel):
    title: str
    model: str
    messages: list[SharedMessage]
    created_at: datetime


@router.get("/shared/{public_id}", response_model=SharedChat)
async def get_shared_chat(public_id: str, db: AsyncSession = Depends(get_db)):
    chat = await db.scalar(select(Chat).where(Chat.public_id == public_id))
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada ou não compartilhada")
    await db.refresh(chat, attribute_names=["messages"])
    msgs = [
        SharedMessage(role=m.role, content=m.content, created_at=m.created_at)
        for m in chat.messages
        if m.role in ("user", "assistant") and (m.content or "").strip() and not m.compacted
    ]
    return SharedChat(title=chat.title, model=chat.model, messages=msgs, created_at=chat.created_at)
