"""Base de Conhecimento (RAG): coleções de documentos indexados p/ recuperação.

Espelha os Bancos de Memória (`memory_bank.py`), mas para RAG sobre o TEXTO LITERAL
dos documentos (não fatos consolidados por LLM). Um modelo acopla bases em
`capabilities.knowledge.bases`; um chat acopla em `chat.knowledge_config.bases`.

Três tabelas:
  - `knowledge_bases`  — a coleção (nome/descrição), acoplável por modelo/chat.
  - `knowledge_docs`   — cada arquivo enviado; guarda os bytes originais (LargeBinary)
    p/ reindexar/baixar, além do status de indexação.
  - `knowledge_chunks` — os pedaços de texto + embedding (coluna `vector(384)` criada
    na migration; NÃO mapeada no ORM — inserida/consultada via SQL cru em `knowledge/`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"

    base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    # pending | indexing | ready | error
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # bytes originais — permite reindexar e baixar o arquivo (como generated_images)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class KnowledgeChunk(Base):
    """Pedaço de texto de um documento + embedding.

    A coluna `embedding vector(384)` é criada na migration e manipulada por SQL cru
    (ver `knowledge/ingest.py` e `knowledge/retrieval.py`) — não é mapeada aqui p/
    não depender do pacote python `pgvector`. Este modelo existe p/ o cascade de FK
    e consultas de metadados.
    """

    __tablename__ = "knowledge_chunks"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_docs.id", ondelete="CASCADE"), index=True
    )
    base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
