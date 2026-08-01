"""Ledger de tarefa: a MEMÓRIA DE TRABALHO estruturada e persistente de um objetivo
multi-turno (um por chat). Diferente da memória (fatos de longo prazo entre chats), do
cérebro (notas interligadas) e do conhecimento (RAG de docs): o ledger é o "estado da
investigação/execução" do objetivo ATUAL — plano com passos, achados com ciclo de vida,
notas de evidência e o próximo passo. É injetado no contexto A CADA turno pra o agente
NÃO re-derivar, convergir, e não re-reportar um achado já refutado.

Agnóstico de domínio: serve refatoração ("arquivos migrados/faltando, o que quebrou"),
desenvolvimento ("o que construí/testei, próximo passo") e segurança ("vetores tentados,
achados abertos/confirmados/refutados") igualmente.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class TaskLedger(Base):
    __tablename__ = "task_ledgers"

    # um ledger por chat (o objetivo vive na conversa). Projeto opcional (Codespace).
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("codespace_projects.id", ondelete="SET NULL"), nullable=True
    )
    # o que estamos tentando alcançar (uma frase estável)
    objective: Mapped[str] = mapped_column(Text, default="")
    # active | done | paused
    status: Mapped[str] = mapped_column(String(16), default="active")
    # passos do plano: [{"id": str, "text": str, "status": "todo|doing|done|blocked",
    #   "note": str}]. status guia a convergência (todo→doing→done).
    plan: Mapped[list] = mapped_column(JSONB, default=list)
    # achados/hipóteses com CICLO DE VIDA: [{"id", "text", "status": "open|confirmed|
    #   refuted", "evidence": str}]. 'refuted' NÃO pode voltar como prioritário — é o que
    #   mata a ancoragem (reportar o que a própria evidência derruba).
    findings: Mapped[list] = mapped_column(JSONB, default=list)
    # notas de evidência append-only (capadas): [{"ts": iso, "text": str}]
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    # o foco imediato — a próxima ação concreta
    next_step: Mapped[str] = mapped_column(Text, default="")
