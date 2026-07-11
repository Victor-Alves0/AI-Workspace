"""Ledger de uso: uma linha por resposta do assistente com tokens/custo/modelo.

Gravado EM PARALELO à mensagem (não no lugar dela) para que a Analítica sobreviva
à exclusão do chat — por isso `chat_id`/`message_id`/`model_config_id` são UUIDs
SEM foreign key (não são apagados/anulados quando o chat ou o modelo somem). Só o
`user_id` tem FK (CASCADE): se o usuário é removido, o histórico dele também sai.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class UsageEvent(Base):
    __tablename__ = "usage_events"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # referências "soltas" (sem FK) — preservam o histórico após o chat/modelo sumir
    chat_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    model: Mapped[str] = mapped_column(String(255), default="")
    model_name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(64), default="openrouter")

    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    reasoning_tokens: Mapped[int] = mapped_column(default=0)
    cached_tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)

    # tokens (aproximados) por ferramenta usada na resposta: {caminho_da_tool: tokens}
    tools_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
