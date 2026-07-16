"""Rotas de Segurança: 2FA (TOTP) e log de auditoria (Configurações → Segurança)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit_service, twofa_service
from .auth.deps import current_user, require_admin, require_approved
from .auth.security import verify_password
from .db import get_db
from .models import AuditEvent, User

router = APIRouter(prefix="/security", tags=["security"])


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class DisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


# --------------------------------------------------------------------------- #
# 2FA (TOTP)
# --------------------------------------------------------------------------- #
@router.get("/2fa")
async def twofa_status(user: User = Depends(current_user)):
    return {"enabled": user.totp_enabled}


@router.post("/2fa/setup")
async def twofa_setup(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Gera um segredo novo (ainda NÃO ativa) e devolve o QR + o segredo para
    digitação manual. Ativar exige confirmar um código em /2fa/enable."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA já está ativo — desative antes de reconfigurar")
    secret = twofa_service.new_secret()
    user.totp_secret = secret  # cifrado em repouso (EncryptedText); só vale após enable
    await db.commit()
    return {"secret": secret, "qr": twofa_service.qr_data_url(secret, user.email)}


@router.post("/2fa/enable")
async def twofa_enable(
    body: CodeIn, request: Request,
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
):
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA já está ativo")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configure o 2FA primeiro (/2fa/setup)")
    if not twofa_service.verify(user.totp_secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Código inválido — confira o app autenticador")
    user.totp_enabled = True
    await db.commit()
    await audit_service.record("twofa_enabled", user_id=user.id, request=request)
    return {"ok": True}


@router.post("/2fa/disable")
async def twofa_disable(
    body: DisableIn, request: Request,
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
):
    """Desativa o 2FA. Exige a SENHA (não o código): se o usuário perdeu o app
    autenticador, ainda consegue desligar com a senha da conta."""
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha incorreta")
    user.totp_enabled = False
    user.totp_secret = None
    await db.commit()
    await audit_service.record("twofa_disabled", user_id=user.id, request=request)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Log de auditoria
# --------------------------------------------------------------------------- #
def _serialize(ev: AuditEvent) -> dict:
    return {
        "id": str(ev.id),
        "action": ev.action,
        "label": audit_service.ACTION_LABELS.get(ev.action, ev.action),
        "detail": ev.detail or {},
        "ip": ev.ip,
        "user_agent": ev.user_agent,
        "created_at": ev.created_at.isoformat(),
    }


@router.get("/audit")
async def my_audit(
    limit: int = 100,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Os eventos de auditoria DO PRÓPRIO usuário (login, senha, 2FA, segredos…)."""
    rows = await db.scalars(
        select(AuditEvent).where(AuditEvent.user_id == user.id)
        .order_by(desc(AuditEvent.created_at)).limit(max(1, min(int(limit), 500)))
    )
    return [_serialize(e) for e in rows]


@router.get("/audit/all")
async def all_audit(
    limit: int = 200,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Todos os eventos (somente admin) — inclui logins falhos sem usuário."""
    rows = await db.scalars(
        select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(max(1, min(int(limit), 1000)))
    )
    out = []
    for e in rows:
        d = _serialize(e)
        d["user_id"] = str(e.user_id) if e.user_id else None
        out.append(d)
    return out
