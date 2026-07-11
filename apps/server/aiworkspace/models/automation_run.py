"""Histórico de execuções de uma automação.

Cada disparo (agendado OU manual/"Testar") grava uma linha aqui — inclusive as
FALHAS, que hoje só ficavam em `automations.last_error` (sobrescrito). Permite uma
timeline por automação: quando rodou, como foi disparada, o resultado/erro e o link
para a conversa gerada.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    automation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("automations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # ok | error | no_change | skipped
    status: Mapped[str] = mapped_column(String(16), default="ok")
    # scheduled | manual
    trigger: Mapped[str] = mapped_column(String(16), default="scheduled")
    # resumo do resultado (texto da resposta/notificação) — cortado
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # conversa/mensagem gerada (para "abrir" a partir do histórico)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
