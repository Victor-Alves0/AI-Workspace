"""Agente/equipe em SEGUNDO PLANO, registrado para sobreviver a um restart.

Como o Claude Code guarda a transcrição de cada subagente para poder retomá-lo: aqui
fica tudo o que é preciso para rodar o trabalho de novo (`spec`: membros, tarefa,
configuração do runner) e o que já terminou (`results`: checkpoint por membro). No
boot, `subagent_jobs.recover()` retoma os interrompidos só com o que faltava e entrega
os que terminaram sem chegar a acordar o chat. Ver chat/subagent_jobs.py.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SubagentJob(Base):
    __tablename__ = "subagent_jobs"

    job_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))           # agent | team
    name: Mapped[str] = mapped_column(String(200), default="")
    task: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    results: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # running | done | failed — `delivered`: o relatório já acordou o chat
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)     # nota do wake (pronta)
    card: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # resultado p/ o card
