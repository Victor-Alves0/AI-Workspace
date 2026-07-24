"""Rastros de observabilidade: traces (operações) e spans (etapas).

Persistido para diagnóstico fino de qualquer chamada — latência, tempo de banco,
leituras/escritas, tempo de conexão HTTP. Guarda METADADOS, não conteúdo: nunca o
texto de mensagens/prompts/segredos (a menos que o admin ligue a captura).

`user_id` tem FK CASCADE de propósito: apagar a conta apaga o rastro dela também
(privacidade). Traces pré-login (tentativa de autenticação) têm `user_id` nulo e
não pertencem a ninguém. `spans` cascateia a partir do trace.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ObsTrace(Base):
    __tablename__ = "obs_traces"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(32), default="http")  # http|chat|api|automation|channel|worker
    method: Mapped[str] = mapped_column(String(16), default="", server_default="")
    path: Mapped[str] = mapped_column(String(300), default="", server_default="")
    status_code: Mapped[int] = mapped_column(default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(8), default="ok")  # ok|error
    error: Mapped[str] = mapped_column(Text, default="", server_default="")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    span_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # somatórios do trace inteiro (evita reagregar spans para a lista)
    db_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    db_queries: Mapped[int] = mapped_column(default=0, server_default="0")
    http_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    llm_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    attrs: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (
        Index("ix_obs_traces_kind_started", "kind", "started_at"),
        Index("ix_obs_traces_user_started", "user_id", "started_at"),
        Index("ix_obs_traces_status_started", "status", "started_at"),
    )


class ObsSpan(Base):
    __tablename__ = "obs_spans"

    trace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("obs_traces.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(32), default="internal")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # deslocamento (ms) do início do span em relação ao início do trace — é o que
    # o "waterfall" usa para posicionar a barra sem recalcular a partir de epochs
    offset_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(8), default="ok")
    error: Mapped[str] = mapped_column(Text, default="", server_default="")

    db_reads: Mapped[int] = mapped_column(default=0, server_default="0")
    db_writes: Mapped[int] = mapped_column(default=0, server_default="0")
    db_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    http_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    attrs: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
