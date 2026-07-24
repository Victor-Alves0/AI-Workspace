"""Trilha de auditoria de ações sensíveis (Configurações → Segurança → Logs).

`record` grava um evento (login, troca de senha, segredo alterado, 2FA, backup…).
É best-effort: uma falha ao auditar NUNCA derruba a operação auditada. A escrita
usa uma sessão PRÓPRIA (SessionLocal), então funciona mesmo dentro de fluxos que
já vão dar rollback/commit por conta própria (ex.: login que levanta 401)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request

from .db import SessionLocal
from .models import AuditEvent

logger = logging.getLogger(__name__)

# rótulos legíveis por ação (usados pela UI). Ações sem rótulo caem no próprio código.
ACTION_LABELS: dict[str, str] = {
    "login": "Login",
    "login_failed": "Falha de login",
    "login_2fa_required": "Login: 2º fator solicitado",
    "logout": "Logout",
    "password_changed": "Senha alterada",
    "secret_set": "Chave/segredo alterado",
    "twofa_enabled": "2FA ativado",
    "twofa_disabled": "2FA desativado",
    "backup_exported": "Backup exportado",
    "backup_restored": "Backup restaurado",
    "app_secret_rotated": "Chave mestra rotacionada",
    "account_approved": "Conta aprovada",
    "account_rejected": "Conta rejeitada",
    "api_key_created": "Chave de API criada",
    "api_key_revoked": "Chave de API revogada",
    "api_key_regenerated": "Chave de API regenerada",
    "api_key_deleted": "Chave de API excluída",
}


def _client(request: Request | None) -> tuple[str, str]:
    """(ip, user_agent) de uma request — respeitando X-Forwarded-For se houver proxy."""
    if request is None:
        return "", ""
    xff = request.headers.get("x-forwarded-for", "")
    ip = (xff.split(",")[0].strip() if xff else "") or (request.client.host if request.client else "")
    ua = request.headers.get("user-agent", "")
    return ip[:64], ua[:255]


async def record(
    action: str,
    *,
    user_id: uuid.UUID | str | None = None,
    request: Request | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Registra um evento de auditoria. Nunca levanta."""
    ip, ua = _client(request)
    uid: uuid.UUID | None
    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else (uuid.UUID(str(user_id)) if user_id else None)
    except (ValueError, TypeError):
        uid = None
    try:
        async with SessionLocal() as db:
            db.add(AuditEvent(user_id=uid, action=action[:64], detail=detail or {}, ip=ip, user_agent=ua))
            await db.commit()
    except Exception:  # noqa: BLE001 - auditoria é best-effort
        logger.warning("falha ao gravar evento de auditoria '%s'", action)
