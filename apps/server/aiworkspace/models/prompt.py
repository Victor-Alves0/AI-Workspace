"""Prompts reutilizáveis (estilo "Prompts" do OpenWebUI).

Cada prompt tem um comando (ex.: "break"), um nome e um conteúdo. No chat, o
usuário digita `/comando` e o conteúdo é transcrito para o campo de mensagem
(não é enviado direto ao modelo).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("user_id", "command", name="uq_prompt_command"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # comando sem a barra, ex.: "break" (invocado como "/break")
    command: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
