"""Propostas de skill sugeridas pela IA (Aprendizado Proativo / Curator).

A revisão em background (chat/curator.py) NUNCA cria uma Skill direto — grava aqui
uma PROPOSTA pendente. O usuário revê na aba Skills e aprova (vira Skill) ou descarta.
É o gate de consentimento que nos diferencia do auto-save do Hermes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SkillProposal(Base):
    __tablename__ = "skill_proposals"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # conversa que originou a proposta (para "abrir" o contexto); pode sumir
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # arquivos de referência da proposta (import de pasta traz references/*) —
    # [{"name","content"}]; viram Skill.files ao aprovar.
    files: Mapped[list] = mapped_column(JSONB, default=list)
    # por que a IA sugeriu isto (o gatilho na conversa) — mostrado na UI de Sugestões
    rationale: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    # origem da proposta (curator | ...) — para futuras fontes
    source: Mapped[str] = mapped_column(String(32), default="curator")
    # pending | approved | dismissed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
