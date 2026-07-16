"""Integração Discord: bots conectados + conversas mapeadas a chats.

Espelha o Telegram ([[telegram.py]]), mas via Gateway (WebSocket) em vez de
long-polling. Cada usuário conecta um bot (token do Developer Portal) e associa a
um modelo. Mensagens recebidas passam pelos filtros e viram turnos do modelo; a
resposta volta pelo mesmo bot. Cada canal/DM do Discord vira um Chat normal do app.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class DiscordConnection(Base):
    __tablename__ = "discord_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120), default="")
    # token do bot (Developer Portal), cifrado em repouso
    bot_token: Mapped[str] = mapped_column(EncryptedText, default="")
    bot_username: Mapped[str] = mapped_column(String(64), default="")
    # id da aplicação/bot no Discord (p/ detectar @menção ao próprio bot)
    app_id: Mapped[str] = mapped_column(String(32), default="")
    # modelo que atende este bot (custom ou base)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(255), default="")
    # filtragem ANTES do modelo:
    #   {allow: [ids/usernames], block: [...], guilds: bool, mention_only: bool,
    #    trigger: "" (prefixo)}
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    # memória: "local" (isolada por conversa) | "global" (memória do modelo)
    memory: Mapped[str] = mapped_column(String(8), default="local")
    # prompt adicional deste bot, concatenado ao system do modelo
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    # "Modo humanizador": {enabled, typing, min_seconds, max_seconds, split}
    humanize: Mapped[dict] = mapped_column(JSONB, default=dict)
    # pasta "Chats" desta conexão (Discord/<bot>/Chats) — criada sob demanda
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # janela de silencio (s) p/ colar mensagens fragmentadas do contato num unico
    # turno; 0 = desligado (um turno por mensagem)
    debounce_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # quantas mensagens anteriores da conversa a IA enxerga a cada resposta.
    # 0 = "Tudo" (teto de seguranca aplicado no service); padrao 40.
    context_window: Mapped[int] = mapped_column(Integer, default=40, server_default="40")
    # estado vivo do Gateway: {status, last_error, last_event_at,
    #   session_id, resume_url, seq} — permite RESUME após reconexão
    state: Mapped[dict] = mapped_column(JSONB, default=dict)


class DiscordThread(Base):
    """Um canal/DM do Discord ↔ um Chat do app."""

    __tablename__ = "discord_threads"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("discord_connections.id", ondelete="CASCADE"), index=True
    )
    # id do canal no Discord (DM ou canal de servidor)
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    is_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
