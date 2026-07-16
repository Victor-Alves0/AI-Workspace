"""Usuários e segredos por usuário."""

from __future__ import annotations

import uuid

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..crypto import EncryptedText
from ..db import Base


class User(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    # "admin" | "user" — o primeiro usuário cadastrado vira admin.
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "active" | "pending" | "rejected" — novos cadastros ficam pendentes de aprovação.
    status: Mapped[str] = mapped_column(String(16), default="active")
    # modelo padrão (id OpenRouter ou "custom:<uuid>") usado ao iniciar um chat
    default_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # perfil e preferências da UI (nome, sobre, gênero, nascimento, webhook, tema,
    # idioma, notificações, prompt do sistema global) — editados em Configurações
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    # versão de token: incrementar invalida TODOS os JWTs emitidos antes (o `tv` do
    # token precisa bater com este valor). Usado p/ revogar sessões ao trocar a
    # senha ou em "sair de todos os dispositivos".
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 2FA (TOTP): segredo base32 cifrado em repouso; `totp_enabled` só vira True
    # após o usuário confirmar um código válido (prova que pareou o app).
    totp_secret: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    secrets: Mapped[list["UserSecret"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuditEvent(Base):
    """Trilha de auditoria de ações sensíveis (Configurações → Segurança → Logs)."""

    __tablename__ = "audit_events"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class UserSecret(Base):
    """Segredo cifrado (ex.: 'openrouter_api_key')."""

    __tablename__ = "user_secrets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_secret_name"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[str] = mapped_column(String)

    user: Mapped["User"] = relationship(back_populates="secrets")
