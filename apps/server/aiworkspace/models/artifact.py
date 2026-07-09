"""Artefatos: conteúdos estruturados que a IA produz numa janela dedicada.

Um artefato pertence a um chat e é identificado por um slug estável
(`identifier`) — o modelo REUSA o mesmo identifier para atualizar em vez de
recriar. Cada mudança (da IA ou do usuário) gera uma versão no histórico.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    identifier: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(255), default="")
    # code | markdown | html | svg | mermaid | json | csv | text
    kind: Mapped[str] = mapped_column(String(24), default="text")
    language: Mapped[str] = mapped_column(String(40), default="")
    # conteúdo da versão ATUAL (cifrado em repouso)
    content: Mapped[str] = mapped_column(EncryptedText, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(EncryptedText, default="")
    # quem produziu esta versão: "ai" | "user" | "restore"
    label: Mapped[str] = mapped_column(String(16), default="ai")
