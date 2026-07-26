"""Integração Slack como CANAL: workspaces conectados + conversas mapeadas a chats.

Espelha o [[discord.py]], mas o transporte é o Socket Mode do Slack (WebSocket que
o app ABRE — sem URL pública, igual ao Gateway do Discord). Cada usuário conecta um
app do Slack (Bot Token xoxb + App-Level Token xapp com connections:write) e o associa
a um modelo. Mensagens recebidas viram turnos; a resposta volta pelo mesmo bot. Cada
canal/DM do Slack vira um Chat normal do app.

Distinto do `slack_accounts` (a FERRAMENTA slack.workspace.manage): aqui é o canal
conversacional (mirror), lá é a IA agindo no workspace sob demanda.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class SlackChannelConnection(Base):
    __tablename__ = "slack_channel_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120), default="")
    # Bot User OAuth Token (xoxb-…) — envia/lê; cifrado
    bot_token: Mapped[str] = mapped_column(EncryptedText, default="")
    # App-Level Token (xapp-…) com connections:write — abre o Socket Mode; cifrado
    app_token: Mapped[str] = mapped_column(EncryptedText, default="")
    team: Mapped[str] = mapped_column(String(160), default="")
    # id do próprio bot (p/ ignorar as próprias mensagens e detectar @menção)
    bot_user_id: Mapped[str] = mapped_column(String(32), default="")
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(255), default="")
    # {allow, block, channels: bool, mention_only: bool, trigger: ""}
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    memory: Mapped[str] = mapped_column(String(8), default="local")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    humanize: Mapped[dict] = mapped_column(JSONB, default=dict)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    debounce_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    context_window: Mapped[int] = mapped_column(Integer, default=40, server_default="40")
    # estado vivo do Socket Mode: {status, last_error, last_event_at}
    state: Mapped[dict] = mapped_column(JSONB, default=dict)


class SlackChannelThread(Base):
    """Um canal/DM do Slack ↔ um Chat do app."""

    __tablename__ = "slack_channel_threads"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("slack_channel_connections.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    is_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
