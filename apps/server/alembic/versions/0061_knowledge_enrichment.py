"""knowledge_enrichments — propostas de tags/descrição geradas pela IA

O Enriquecedor com IA gera título/descrição/tags para os documentos (visão p/ imagens,
texto p/ docs) e grava aqui como PROPOSTA; o usuário aprova (vira KnowledgeDoc.meta +
reindexa) ou descarta. Uma linha por doc em processamento.

Revision ID: 0061_knowledge_enrichment
Revises: 0060_skill_proposal_rationale
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0061_knowledge_enrichment"
down_revision: Union[str, None] = "0060_skill_proposal_rationale"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_enrichments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("base_id", sa.Uuid(), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("extra_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doc_id"], ["knowledge_docs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_enrichments_user_id", "knowledge_enrichments", ["user_id"])
    op.create_index("ix_knowledge_enrichments_base_id", "knowledge_enrichments", ["base_id"])
    op.create_index("ix_knowledge_enrichments_doc_id", "knowledge_enrichments", ["doc_id"])
    op.create_index("ix_knowledge_enrichments_status", "knowledge_enrichments", ["status"])


def downgrade() -> None:
    op.drop_table("knowledge_enrichments")
