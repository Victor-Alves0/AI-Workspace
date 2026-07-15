"""Rotas da integração Discord: conectar um bot (Developer Portal) e configurá-lo.

Por-usuário (cada um conecta o próprio bot). Conectar valida o token via GET
/users/@me, guarda o username/app_id e sobe o gateway (WebSocket). Espelha as rotas
do Telegram.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .integrations import discord_api, discord_gateway
from .models import DiscordConnection, DiscordThread, User

router = APIRouter(prefix="/integrations/discord", tags=["discord"])


class ConnIn(BaseModel):
    label: str = Field(default="", max_length=120)
    bot_token: str = Field(default="", max_length=200)
    model_config_id: uuid.UUID | None = None
    model: str = Field(default="", max_length=255)
    filters: dict[str, Any] = Field(default_factory=dict)
    memory: str = Field(default="local")
    system_prompt: str = Field(default="", max_length=8000)
    humanize: dict[str, Any] = Field(default_factory=dict)
    debounce_seconds: int = Field(default=0, ge=0, le=60)


class ConnUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    bot_token: str | None = Field(default=None, max_length=200)
    model_config_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=255)
    filters: dict[str, Any] | None = None
    memory: str | None = None
    system_prompt: str | None = Field(default=None, max_length=8000)
    humanize: dict[str, Any] | None = None
    debounce_seconds: int | None = Field(default=None, ge=0, le=60)
    enabled: bool | None = None


class ConnOut(BaseModel):
    id: str
    label: str
    bot_username: str
    model_config_id: str | None
    model: str
    filters: dict
    memory: str
    system_prompt: str
    humanize: dict
    debounce_seconds: int
    enabled: bool
    state: dict
    threads: int = 0


def _out(c: DiscordConnection, threads: int = 0) -> ConnOut:
    # não vaza campos internos do gateway (session_id/seq/resume_url) p/ a UI
    st = {k: v for k, v in (c.state or {}).items()
          if k in ("status", "last_error", "last_event_at")}
    return ConnOut(
        id=str(c.id), label=c.label, bot_username=c.bot_username,
        model_config_id=str(c.model_config_id) if c.model_config_id else None,
        model=c.model, filters=c.filters or {}, memory=c.memory,
        system_prompt=c.system_prompt or "", humanize=c.humanize or {},
        debounce_seconds=c.debounce_seconds or 0,
        enabled=c.enabled, state=st, threads=threads,
    )


async def _owned(db: AsyncSession, conn_id: uuid.UUID, user: User) -> DiscordConnection:
    c = await db.get(DiscordConnection, conn_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conexão não encontrada")
    return c


async def _validate_token(token: str) -> tuple[str, str]:
    """Valida o token via GET /users/@me. Retorna (username, app_id). 400 se inválido."""
    try:
        me = await discord_api.get_me(token.strip())
    except discord_api.DiscordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Token inválido: {exc}")
    return me.get("username") or "", str(me.get("id") or "")


@router.get("/connections", response_model=list[ConnOut])
async def list_connections(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(
        select(DiscordConnection).where(DiscordConnection.user_id == user.id).order_by(DiscordConnection.created_at)
    ))
    out = []
    for c in rows:
        cnt = await db.scalar(
            select(func.count()).select_from(DiscordThread).where(DiscordThread.connection_id == c.id)
        )
        out.append(_out(c, int(cnt or 0)))
    return out


@router.post("/connections", response_model=ConnOut)
async def create_connection(body: ConnIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    if not body.bot_token.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o token do bot (Developer Portal)")
    username, app_id = await _validate_token(body.bot_token)
    c = DiscordConnection(
        user_id=user.id, label=body.label or username or "Bot",
        bot_token=body.bot_token.strip(), bot_username=username, app_id=app_id,
        model_config_id=body.model_config_id, model=body.model,
        filters=body.filters, memory=body.memory, system_prompt=body.system_prompt,
        humanize=body.humanize, debounce_seconds=body.debounce_seconds, enabled=True, state={"status": "connecting"},
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    await discord_gateway.refresh(c.id)
    return _out(c)


@router.patch("/connections/{conn_id}", response_model=ConnOut)
async def update_connection(
    conn_id: uuid.UUID, body: ConnUpdate,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    c = await _owned(db, conn_id, user)
    data = body.model_dump(exclude_unset=True)
    if "bot_token" in data and data["bot_token"] and data["bot_token"].strip():
        c.bot_username, c.app_id = await _validate_token(data["bot_token"])
        c.bot_token = data["bot_token"].strip()
        # troca de token invalida a sessão do gateway
        c.state = {**(c.state or {}), "session_id": None, "resume_url": None, "seq": None}
    data.pop("bot_token", None)
    for k, v in data.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    await discord_gateway.refresh(c.id)
    return _out(c)


@router.post("/connections/{conn_id}/toggle", response_model=ConnOut)
async def toggle_connection(conn_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    c = await _owned(db, conn_id, user)
    c.enabled = not c.enabled
    if c.enabled:
        c.state = {**(c.state or {}), "last_error": None}
    await db.commit()
    await db.refresh(c)
    await discord_gateway.refresh(c.id)
    return _out(c)


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    c = await _owned(db, conn_id, user)
    from .integrations.discord_gateway import _cancel
    _cancel(c.id)
    await db.delete(c)
    await db.commit()
    return {"ok": True}


class TestIn(BaseModel):
    channel_id: str = Field(default="", max_length=32)
    text: str = Field(default="Mensagem de teste do AI Workspace ✅", max_length=2000)


@router.post("/connections/{conn_id}/test")
async def test_send(
    conn_id: uuid.UUID, body: TestIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Envia uma mensagem de teste. Sem channel_id, usa a última conversa conhecida."""
    c = await _owned(db, conn_id, user)
    target = body.channel_id.strip()
    if not target:
        t = await db.scalar(
            select(DiscordThread.channel_id).where(DiscordThread.connection_id == c.id)
            .order_by(DiscordThread.last_message_at.desc().nullslast()).limit(1)
        )
        target = t or ""
    if not target:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhuma conversa ainda — fale com o bot primeiro, ou informe um channel_id.")
    try:
        await discord_api.send_message(c.bot_token, target, body.text)
    except discord_api.DiscordError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Falha ao enviar: {exc}")
    return {"ok": True, "channel_id": target}
