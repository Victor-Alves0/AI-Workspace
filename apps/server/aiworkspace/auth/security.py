"""Hashing de senha (Argon2) e tokens JWT (access + refresh)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError

from ..config import get_settings

_ph = PasswordHasher()
_ALGO = "HS256"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, Argon2Error):
        # hash inválido/corrompido conta como credencial errada, nunca como 500
        return False


# hash fixo usado para IGUALAR o tempo de resposta quando o e-mail não existe —
# sem isso, "usuário inexistente" responde muito mais rápido que "senha errada"
# (o Argon2 só roda p/ usuários reais), permitindo enumeração por timing.
_DUMMY_HASH = _ph.hash("aw-timing-equalizer")


def dummy_verify() -> None:
    """Consome ~o mesmo tempo de um verify real (contra enumeração por timing)."""
    try:
        _ph.verify(_DUMMY_HASH, "invalid-password")
    except Exception:  # noqa: BLE001
        pass


def _create_token(sub: str, ttl: timedelta, token_type: str, token_version: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "type": token_type,
        "tv": int(token_version),  # versão de token p/ revogação (ver models.User)
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.app_secret, algorithm=_ALGO)


def create_access_token(user_id: str, token_version: int = 0) -> str:
    s = get_settings()
    return _create_token(user_id, timedelta(minutes=s.access_token_ttl_min), "access", token_version)


def create_refresh_token(user_id: str, token_version: int = 0) -> str:
    s = get_settings()
    return _create_token(user_id, timedelta(days=s.refresh_token_ttl_days), "refresh", token_version)


def decode_token(token: str, expected_type: str) -> tuple[str, int]:
    """Retorna (sub, token_version) se válido; levanta jwt exceptions caso contrário."""
    settings = get_settings()
    payload = jwt.decode(token, settings.app_secret, algorithms=[_ALGO])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("tipo de token inválido")
    return payload["sub"], int(payload.get("tv", 0))
