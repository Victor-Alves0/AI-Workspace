"""Integração WhatsApp: números conectados + conversas mapeadas a chats.

Cada usuário conecta um ou mais números (provider "evolution" = não oficial via
QR Code / "official" = Meta Cloud API) e associa cada número a um modelo de IA.
Mensagens recebidas passam pelos filtros da conexão e viram turnos do modelo; a
resposta volta pelo mesmo provedor. Cada conversa (jid) vira um Chat normal do
app, então o dono acompanha tudo pela sidebar.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class WhatsAppConnection(Base):
    __tablename__ = "whatsapp_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120), default="")
    # "evolution" (não oficial, QR Code via Evolution API) | "official" (Meta Cloud API)
    provider: Mapped[str] = mapped_column(String(16), default="evolution")
    # número conectado (preenchido quando a sessão abre / informado no oficial)
    phone: Mapped[str] = mapped_column(String(32), default="")
    # nome da instância no Evolution (provider "evolution")
    instance: Mapped[str] = mapped_column(String(64), default="")
    # credenciais do Cloud API (provider "official"); tokens cifrados em repouso
    phone_number_id: Mapped[str] = mapped_column(String(64), default="")
    access_token: Mapped[str] = mapped_column(EncryptedText, default="")
    app_secret: Mapped[str] = mapped_column(EncryptedText, default="")
    # verify token do webhook da Meta (mostrado ao usuário p/ colar no console)
    verify_token: Mapped[str] = mapped_column(String(64), default="")
    # segredo da URL do webhook (rota pública identificada por ele, sem login)
    webhook_token: Mapped[str] = mapped_column(String(64), default="", index=True)
    # modelo que atende este número (custom ou base)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(255), default="")
    # camada de filtragem ANTES do modelo:
    #   {policy: "all"|"allow"|"block", allow: [nums], block: [nums],
    #    groups: bool (responder em grupos), trigger: "" (prefixo obrigatório)}
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    # memória das conversas: "local" (isolada por conversa) | "global" (alimenta a
    # memória compartilhada do modelo, junto com os outros canais)
    memory: Mapped[str] = mapped_column(String(8), default="local")
    # prompt adicional DESTE número, concatenado ao system prompt do modelo
    # (ex.: "responda curto, sem markdown, informal")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    # limites de mensagens por número que entra em contato (0/ausente = sem limite):
    #   {total: int, per_hour: int, per_day: int, per_month: int}
    limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    # contexto/roles por contato — vai ao modelo quando o número conversa:
    #   [{number, name, role, context}]
    contacts: Mapped[list] = mapped_column(JSONB, default=list)
    # pasta "Chats" desta conexão (WhatsApp/<número>/Chats) — criada sob demanda
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # estado vivo: {status, profile_name, last_error, last_event_at}
    state: Mapped[dict] = mapped_column(JSONB, default=dict)


class WhatsAppThread(Base):
    """Uma conversa do WhatsApp (contato ou grupo) ↔ um Chat do app."""

    __tablename__ = "whatsapp_threads"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), index=True
    )
    # jid do WhatsApp: 5511999999999@s.whatsapp.net (contato) | ...@g.us (grupo)
    jid: Mapped[str] = mapped_column(String(128))
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
