"""Leitura pública (sem auth) de um chat compartilhado.

`GET /shared/{public_id}` devolve o chat em modo read-only: título + mensagens
(papel/conteúdo/hora), sem nada sensível. Só funciona enquanto o dono mantém o
`public_id` setado (revogar = 404). Mensagens compactadas/resumo são omitidas.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.security import verify_password
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
async def get_shared_chat(public_id: str, pw: str = "", db: AsyncSession = Depends(get_db)):
    chat = await db.scalar(select(Chat).where(Chat.public_id == public_id))
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada ou não compartilhada")
    # validade do link (não apaga o chat, só invalida o acesso público)
    if chat.public_expires_at and chat.public_expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_410_GONE, "Este link de compartilhamento expirou")
    # senha opcional: sem/errada → 401 com sinalização p/ o front pedir a senha
    if chat.public_password_hash:
        if not pw or not verify_password(pw, chat.public_password_hash):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"protected": True, "message": "Senha necessária para ver esta conversa"},
            )
    await db.refresh(chat, attribute_names=["messages"])
    msgs = [
        SharedMessage(role=m.role, content=m.content, created_at=m.created_at)
        for m in chat.messages
        if m.role in ("user", "assistant") and (m.content or "").strip() and not m.compacted
    ]
    return SharedChat(title=chat.title, model=chat.model, messages=msgs, created_at=chat.created_at)
