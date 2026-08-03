"""Saúde das capacidades do harness: um registro estruturado toda vez que uma
capacidade CENTRAL degrada ou faz fallback — o que hoje só vira um `warning` solto
e passa silencioso (foi como o mem0 rodou em no-op sem ninguém notar).

Diferente de `obs_traces` (latência/erro por REQUISIÇÃO), aqui o eixo é a
COMPETÊNCIA do sistema: "com que frequência a síntese caiu p/ a camada C?", "o mem0
está em no-op?", "quantas tools o watchdog abortou hoje?". É o substrato da medição
de primitivos e dos alarmes de auto-observabilidade.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class HealthEvent(Base):
    __tablename__ = "health_events"

    # capacidade afetada: memory | synthesis | tool_watchdog | codegraph |
    # output_guard | guard_judge | compaction | embedder | database | browser | ...
    capability: Mapped[str] = mapped_column(String(32), default="", index=True)
    # o que aconteceu (código curto): no_op | tier_b | tier_c | empty_answer |
    # abort | deadline_hit | swap | trip | down | recovered | ok
    event: Mapped[str] = mapped_column(String(48), default="")
    # info (nota) | warn (fallback leve) | degraded (capacidade caída/rebaixada) |
    # error (falha). Alarmes disparam em degraded|error.
    severity: Mapped[str] = mapped_column(String(12), default="info", index=True)
    # eventos de SISTEMA (boot self-check, mem0 global) não têm dono; os por-turno têm
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    # contexto livre: {"tool": ..., "model": ..., "elapsed_ms": ..., "reason": ...}
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_health_events_cap_created", "capability", "created_at"),
        Index("ix_health_events_sev_created", "severity", "created_at"),
    )
