"""Propostas de enriquecimento de documentos da Base de Conhecimento.

O "Enriquecedor com IA" (knowledge/enrich.py) NUNCA escreve o `meta` do doc direto:
grava aqui uma PROPOSTA (título/descrição/tags gerados pela IA) e o usuário aprova
(vira `KnowledgeDoc.meta` + reindexa) ou descarta. Mesmo gate de consentimento das
[[skill_proposals]]. Uma linha por doc em processamento; some ao aprovar/descartar.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class KnowledgeEnrichment(Base):
    __tablename__ = "knowledge_enrichments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"), index=True
    )
    # pending (na fila/gerando) | ready (proposta pronta p/ revisão) | error
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # proposta gerada pela IA (preenchida quando status=ready)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    # modelo usado + instrução extra do usuário ("põe a tag amarelo em quem tem carro")
    model: Mapped[str] = mapped_column(String(255), default="")
    extra_prompt: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
