"""Rotas do CANAL Slack: conectar um app (Bot Token + App-Level Token) via Socket Mode.

Por-usuário. Conectar valida o Bot Token via auth.test, guarda team/bot_user_id e sobe
o socket (WebSocket). Espelha as rotas do Discord. Distinto de /integrations/slack
(a FERRAMENTA slack.workspace.manage) — aqui o Slack é um canal conversacional.
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
from .integrations import slack_channel_api, slack_socket
from .models import SlackChannelConnection, SlackChannelThread, User

router = APIRouter(prefix="/integrations/slack-channel", tags=["slack-channel"])


class ConnIn(BaseModel):
    label: str = Field(default="", max_length=120)
    bot_token: str = Field(default="", max_length=200)
    app_token: str = Field(default="", max_length=200)
    model_config_id: uuid.UUID | None = None
    model: str = Field(default="", max_length=255)
    filters: dict[str, Any] = Field(default_factory=dict)
    memory: str = Field(default="local")
    system_prompt: str = Field(default="", max_length=8000)
    humanize: dict[str, Any] = Field(default_factory=dict)
    debounce_seconds: int = Field(default=0, ge=0, le=60)
    context_window: int = Field(default=40, ge=0, le=500)


class ConnUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    bot_token: str | None = Field(default=None, max_length=200)
    app_token: str | None = Field(default=None, max_length=200)
    model_config_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=255)
    filters: dict[str, Any] | None = None
    memory: str | None = None
    system_prompt: str | None = Field(default=None, max_length=8000)
    humanize: dict[str, Any] | None = None
    debounce_seconds: int | None = Field(default=None, ge=0, le=60)
    context_window: int | None = Field(default=None, ge=0, le=500)
    enabled: bool | None = None


class ConnOut(BaseModel):
    id: str
    label: str
    team: str
    model_config_id: str | None
    model: str
    filters: dict
    memory: str
    system_prompt: str
    humanize: dict
    debounce_seconds: int
    context_window: int
    enabled: bool
    state: dict
    threads: int = 0


def _out(c: SlackChannelConnection, threads: int = 0) -> ConnOut:
    st = {k: v for k, v in (c.state or {}).items()
          if k in ("status", "last_error", "last_event_at")}
    return ConnOut(
        id=str(c.id), label=c.label, team=c.team,
        model_config_id=str(c.model_config_id) if c.model_config_id else None,
        model=c.model, filters=c.filters or {}, memory=c.memory,
        system_prompt=c.system_prompt or "", humanize=c.humanize or {},
        debounce_seconds=c.debounce_seconds or 0,
        context_window=c.context_window if c.context_window is not None else 40,
        enabled=c.enabled, state=st, threads=threads,
    )


async def _owned(db: AsyncSession, conn_id: uuid.UUID, user: User) -> SlackChannelConnection:
    c = await db.get(SlackChannelConnection, conn_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conexão não encontrada")
    return c


async def _validate(bot_token: str) -> tuple[str, str]:
    """Valida o Bot Token via auth.test. Retorna (team, bot_user_id). 400 se inválido."""
    try:
        info = await slack_channel_api.auth_test(bot_token.strip())
    except slack_channel_api.SlackChannelError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bot Token inválido: {exc}")
    return info.get("team") or "", info.get("bot_user_id") or ""


async def _validate_app_token(app_token: str) -> None:
    """Confere o App-Level Token (xapp-) e o Socket Mode via apps.connections.open —
    a URL devolvida é descartada (expira sozinha). Falha cedo com mensagem clara em
    vez de o socket ficar em loop de erro no background."""
    try:
        await slack_channel_api.open_socket_url(app_token.strip())
    except slack_channel_api.SlackChannelError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"App-Level Token inválido ou Socket Mode desativado: {exc}",
        )


@router.get("/connections", response_model=list[ConnOut])
async def list_connections(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(
        select(SlackChannelConnection).where(SlackChannelConnection.user_id == user.id)
        .order_by(SlackChannelConnection.created_at)
    ))
    out = []
    for c in rows:
        cnt = await db.scalar(
            select(func.count()).select_from(SlackChannelThread)
            .where(SlackChannelThread.connection_id == c.id)
        )
        out.append(_out(c, int(cnt or 0)))
    return out


@router.post("/connections", response_model=ConnOut)
async def create_connection(body: ConnIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    if not body.bot_token.strip() or not body.app_token.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o Bot Token (xoxb-) e o App-Level Token (xapp-)")
    team, bot_user_id = await _validate(body.bot_token)
    await _validate_app_token(body.app_token)
    c = SlackChannelConnection(
        user_id=user.id, label=body.label or team or "Slack",
        bot_token=body.bot_token.strip(), app_token=body.app_token.strip(),
        team=team, bot_user_id=bot_user_id,
        model_config_id=body.model_config_id, model=body.model,
        filters=body.filters, memory=body.memory, system_prompt=body.system_prompt,
        humanize=body.humanize, debounce_seconds=body.debounce_seconds,
        context_window=body.context_window, enabled=True, state={"status": "connecting"},
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    await slack_socket.refresh(c.id)
    return _out(c)


@router.patch("/connections/{conn_id}", response_model=ConnOut)
async def update_connection(
    conn_id: uuid.UUID, body: ConnUpdate,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    c = await _owned(db, conn_id, user)
    data = body.model_dump(exclude_unset=True)
    if data.get("bot_token") and data["bot_token"].strip():
        c.team, c.bot_user_id = await _validate(data["bot_token"])
        c.bot_token = data["bot_token"].strip()
    data.pop("bot_token", None)
    if data.get("app_token") and data["app_token"].strip():
        await _validate_app_token(data["app_token"])
        c.app_token = data["app_token"].strip()
    data.pop("app_token", None)
    for k, v in data.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    await slack_socket.refresh(c.id)
    return _out(c)


@router.post("/connections/{conn_id}/toggle", response_model=ConnOut)
async def toggle_connection(conn_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    c = await _owned(db, conn_id, user)
    c.enabled = not c.enabled
    if c.enabled:
        c.state = {**(c.state or {}), "last_error": None}
    await db.commit()
    await db.refresh(c)
    await slack_socket.refresh(c.id)
    return _out(c)


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    c = await _owned(db, conn_id, user)
    slack_socket._cancel(c.id)
    await db.delete(c)
    await db.commit()
    return {"ok": True}


class TestIn(BaseModel):
    channel_id: str = Field(default="", max_length=32)
    text: str = Field(default="Mensagem de teste do Singularity AI ✅", max_length=2000)


@router.post("/connections/{conn_id}/test")
async def test_send(
    conn_id: uuid.UUID, body: TestIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    c = await _owned(db, conn_id, user)
    target = body.channel_id.strip()
    if not target:
        t = await db.scalar(
            select(SlackChannelThread.channel_id).where(SlackChannelThread.connection_id == c.id)
            .order_by(SlackChannelThread.last_message_at.desc().nullslast()).limit(1)
        )
        target = t or ""
    if not target:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhuma conversa ainda — fale com o bot primeiro, ou informe um channel_id.")
    try:
        await slack_channel_api.post_message(c.bot_token, target, body.text)
    except slack_channel_api.SlackChannelError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Falha ao enviar: {exc}")
    return {"ok": True, "channel_id": target}
