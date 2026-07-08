"""Automações (agendadas + monitores) e notificações do app.

Uma Automation é uma definição de dois tipos (`kind`):
  - "scheduled": a cada X tempo, um modelo executa uma instrução num chat.
  - "monitor":   um watcher determinístico checa uma fonte (página/busca/preço/RSS)
                 e só aciona a IA quando algo relevante muda.

O "quando rodar" vive no banco (`next_run_at`/`last_run_at`) para o scheduler
retomar sozinho após reinício (durabilidade + catch-up).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class Automation(Base):
    __tablename__ = "automations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="Nova automação")
    # "scheduled" | "monitor"
    kind: Mapped[str] = mapped_column(String(16), default="scheduled")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # modelo que executa a instrução (personalizado + base do OpenRouter)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(255), default="")
    # instrução em linguagem natural (cifrada em repouso)
    instructions: Mapped[str] = mapped_column(EncryptedText, default="")
    # ferramentas habilitadas para esta automação + fixadas (pin, p/ assertividade)
    tool_ids: Mapped[list] = mapped_column(JSONB, default=list)
    pinned_tool_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # destino do resultado: {"mode": "new_each"|"reuse"|"existing", "chat_id"?: uuid}
    target: Mapped[dict] = mapped_column(JSONB, default=dict)
    # opções do turno/chat: {"use_context": bool, "chat_ttl": horas (número) | "view_once"}
    options: Mapped[dict] = mapped_column(JSONB, default=dict)

    # agendada — modos:
    #   {"mode":"interval","every":N,"unit":"minutes"|"hours"|"days"}  (default/legado)
    #   {"mode":"daily","time":"HH:MM","tz_offset":min}                (todo dia)
    #   {"mode":"weekly","days":[0-6 dom..sáb],"time","tz_offset"}     (dias da semana)
    #   {"mode":"monthly","day":1-31,"time","tz_offset"}               (dia do mês)
    # tz_offset = getTimezoneOffset() do navegador (min; UTC = local + offset)
    schedule: Mapped[dict] = mapped_column(JSONB, default=dict)

    # monitor: tipo do watcher + config específica + intervalo de checagem + estado
    watcher_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    watcher_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)

    # agendamento/execução (durabilidade)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    # falhas consecutivas (para backoff) + última mensagem de erro
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    automation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("automations.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(EncryptedText, default="")
    # chat/mensagem para onde a notificação leva (se houver)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
