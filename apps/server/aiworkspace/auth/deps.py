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
    return user


async def require_approved(user: User = Depends(current_user)) -> User:
    """Bloqueia usuários ainda não aprovados pelo admin."""
    if user.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Conta pendente de aprovação pelo admin"
        )
    return user


async def require_admin(user: User = Depends(require_approved)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requer privilégio de admin")
    return user
