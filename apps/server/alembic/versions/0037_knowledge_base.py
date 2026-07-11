"""Base de Conhecimento (RAG): coleções de documentos indexados + chunks vetoriais

Três tabelas espelhando os Bancos de Memória, mas com o TEXTO LITERAL dos documentos:
  - knowledge_bases   — a coleção acoplável (por modelo/chat)
  - knowledge_docs     — cada arquivo enviado (bytes + status de indexação)
  - knowledge_chunks   — pedaços de texto + embedding `vector(384)` (FastEmbed local)

A coluna `embedding` e o índice HNSW são criados via SQL cru (o pacote python
`pgvector` pode não estar disponível; a EXTENSÃO `vector` já existe — o mem0 a usa).
Também adiciona `chats.knowledge_config` (JSONB) p/ acoplar bases por chat.

Revision ID: 0037_knowledge_base
Revises: 0036_whatsapp_humanize
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_knowledge_base"
down_revision: Union[str, None] = "0036_whatsapp_humanize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIMS = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_bases_user_id", "knowledge_bases", ["user_id"])

    op.create_table(
        "knowledge_docs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("base_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("mime", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_docs_base_id", "knowledge_docs", ["base_id"])
    op.create_index("ix_knowledge_docs_user_id", "knowledge_docs", ["user_id"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("base_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["doc_id"], ["knowledge_docs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_chunks_doc_id", "knowledge_chunks", ["doc_id"])
    op.create_index("ix_knowledge_chunks_base_id", "knowledge_chunks", ["base_id"])
    op.create_index("ix_knowledge_chunks_user_id", "knowledge_chunks", ["user_id"])

    # coluna vetorial + índice HNSW (cosine) — via SQL cru
    op.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN embedding vector({_DIMS})")
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.add_column("chats", sa.Column("knowledge_config", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "knowledge_config")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_docs_user_id", table_name="knowledge_docs")
    op.drop_index("ix_knowledge_docs_base_id", table_name="knowledge_docs")
    op.drop_table("knowledge_docs")
    op.drop_index("ix_knowledge_bases_user_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
