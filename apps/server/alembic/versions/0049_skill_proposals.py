"""Fila de propostas de skill sugeridas pela IA (Aprendizado Proativo / Curator).

A revisão em background nunca cria uma Skill direto — grava uma PROPOSTA pendente
aqui; o usuário aprova (vira Skill) ou descarta.

Revision ID: 0049_skill_proposals
Revises: 0048_chat_share_gate
Create Date: 2026-07-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0049_skill_proposals"
down_revision: Union[str, None] = "0048_chat_share_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", JSONB(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="curator"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_skill_proposals_user_id", "skill_proposals", ["user_id"])
    op.create_index("ix_skill_proposals_status", "skill_proposals", ["status"])


def downgrade() -> None:
    op.drop_index("ix_skill_proposals_status", table_name="skill_proposals")
    op.drop_index("ix_skill_proposals_user_id", table_name="skill_proposals")
    op.drop_table("skill_proposals")
