"""Rotas de Automações + Notificações."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .automation import scheduler
from .automation.runner import run_automation
from .db import get_db
from .models import Automation, Notification, User

router = APIRouter(tags=["automations"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class AutomationIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    title: str = Field(default="Nova automação", max_length=255)
    kind: str = Field(default="scheduled", pattern=r"^(scheduled|monitor)$")
    enabled: bool = True
    model_config_id: uuid.UUID | None = None
    model: str = Field(default="", max_length=255)
    instructions: str = Field(default="", max_length=20_000)
    tool_ids: list[str] = Field(default_factory=list)
    pinned_tool_ids: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] = Field(default_factory=dict)
    watcher_type: str | None = None
    watcher_config: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int | None = None


class AutomationUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    title: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    model_config_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=255)
    instructions: str | None = Field(default=None, max_length=20_000)
    tool_ids: list[str] | None = None
    pinned_tool_ids: list[str] | None = None
    target: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    watcher_type: str | None = None
    watcher_config: dict[str, Any] | None = None
    interval_seconds: int | None = None


class AutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: uuid.UUID
    title: str
    kind: str
    enabled: bool
    model_config_id: uuid.UUID | None
    model: str
    instructions: str
    tool_ids: list
    pinned_tool_ids: list
    target: dict
    options: dict
    schedule: dict
    watcher_type: str | None
    watcher_config: dict
    interval_seconds: int | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    run_count: int
    fail_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    automation_id: uuid.UUID | None
    title: str
    body: str
    chat_id: uuid.UUID | None
    message_id: uuid.UUID | None
    read: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _owned(db: AsyncSession, automation_id: uuid.UUID, user: User) -> Automation:
    a = await db.get(Automation, automation_id)
    if a is None or a.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Automação não encontrada")
    return a


def _reschedule(a: Automation) -> None:
    """(Re)calcula o próximo disparo. Desabilitada => sem próximo disparo.
    Monitor => roda logo (estabelece a linha de base no próximo tick). Agendada =>
    espera um intervalo completo (não dispara na hora da criação)."""
    if not a.enabled:
        a.next_run_at = None
    elif a.kind == "reminder" or (a.schedule or {}).get("one_shot"):
        pass  # one-shot (lembrete): mantém o horário definido na criação; nunca reprograma
    elif a.kind == "monitor":
        a.next_run_at = datetime.now(timezone.utc)
    else:
        a.next_run_at = scheduler.compute_next_run(a, datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
# Automations
# --------------------------------------------------------------------------- #
@router.get("/automations", response_model=list[AutomationOut])
async def list_automations(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    rows = await db.scalars(
        select(Automation).where(Automation.user_id == user.id).order_by(Automation.created_at.desc())
    )
    return list(rows)


@router.post("/automations", response_model=AutomationOut)
async def create_automation(
    body: AutomationIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    a = Automation(user_id=user.id, **body.model_dump())
    _reschedule(a)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


@router.patch("/automations/{automation_id}", response_model=AutomationOut)
async def update_automation(
    automation_id: uuid.UUID,
    body: AutomationUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, automation_id, user)
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(a, field, value)
    # mudou o agendamento/estado de ativação => recalcula o próximo disparo
    if {"enabled", "schedule", "interval_seconds"} & set(data.keys()):
        _reschedule(a)
    await db.commit()
    await db.refresh(a)
    return a


@router.delete("/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    automation_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, automation_id, user)
    await db.delete(a)
    await db.commit()


@router.post("/automations/{automation_id}/toggle", response_model=AutomationOut)
async def toggle_automation(
    automation_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    a = await _owned(db, automation_id, user)
    a.enabled = not a.enabled
    a.fail_count = 0
    a.last_error = None
    _reschedule(a)
    await db.commit()
    await db.refresh(a)
    return a


@router.post("/automations/{automation_id}/run")
async def run_now(
    automation_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Dispara a automação na hora (para testar). Roda de forma síncrona e devolve
    o resultado ou o erro."""
    await _owned(db, automation_id, user)  # valida posse
    try:
        result = await run_automation(automation_id)
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    unread: bool = False,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread:
        q = q.where(Notification.read.is_(False))
    q = q.order_by(Notification.created_at.desc()).limit(100)
    rows = await db.scalars(q)
    return list(rows)


@router.get("/notifications/unread_count")
async def unread_count(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    n = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
    )
    return {"count": int(n or 0)}


@router.patch("/notifications/{notification_id}", response_model=NotificationOut)
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    n = await db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notificação não encontrada")
    n.read = True
    await db.commit()
    await db.refresh(n)
    return n


@router.post("/notifications/read-all")
async def read_all(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    rows = await db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read.is_(False)
        )
    )
    for n in rows:
        n.read = True
    await db.commit()
    return {"ok": True}


@router.delete("/notifications")
async def clear_notifications(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Apaga TODAS as notificações do usuário (limpar a lista)."""
    res = await db.execute(delete(Notification).where(Notification.user_id == user.id))
    await db.commit()
    return {"ok": True, "deleted": res.rowcount}
