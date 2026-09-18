"""Dependências de autenticação para as rotas."""

from __future__ import annotations

import uuid

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from .security import decode_token

ACCESS_COOKIE = "aw_access"
REFRESH_COOKIE = "aw_refresh"


async def current_user(
    aw_access: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not aw_access:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Não autenticado")
    try:
        user_id, token_version = decode_token(aw_access, "access")
        uid = uuid.UUID(user_id)
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado")

    user = await db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inválido")
    # revogação: tokens emitidos antes de um bump de token_version não valem mais
    if token_version != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão revogada; faça login novamente")
    # amarra o trace corrente ao usuário (o middleware abriu sem saber quem era)
    try:
        from .. import tracing
        tracing.set_trace_user(str(user.id))
    except Exception:  # noqa: BLE001
        pass
    return user


async def optional_current_user(
    aw_access: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Versão não-explosiva de ``current_user`` para mídia com acesso público opcional.

    Uma URL privada não ganha acesso só por ter sido assinada: ela ainda precisa da
    sessão do dono. Já uma URL emitida por um chat compartilhado usa um token de
    compartilhamento separado, validado pela própria rota de mídia.
    """
    if not aw_access:
        return None
    try:
        user_id, token_version = decode_token(aw_access, "access")
        user = await db.get(User, uuid.UUID(user_id))
    except (jwt.PyJWTError, ValueError):
        return None
    if user is None or not user.is_active or token_version != user.token_version:
        return None
    return user


async def require_approved(user: User = Depends(current_user)) -> User:
    """Bloqueia usuários ainda não aprovados pelo admin."""
    if user.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Conta pendente de aprovação pelo admin"
        )
    return user


async def optional_approved_user(user: User | None = Depends(optional_current_user)) -> User | None:
    """Usuário ativo quando há sessão; ``None`` para visitante/anônimo."""
    return user if user is not None and user.status == "active" else None


async def require_admin(user: User = Depends(require_approved)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requer privilégio de admin")
    return user
