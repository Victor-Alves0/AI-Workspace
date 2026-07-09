"""Bancos de memória: memória compartilhável entre modelos (fora do escopo global)

Um banco é uma coleção nomeada de memórias. Vários modelos podem "acoplar" o
mesmo banco e assim compartilhar memórias entre si SEM usar o escopo global.
As memórias do banco vivem no mem0 com agent_id "bank:<id>"; aqui só guardamos
o registro (nome/descrição) para criar/listar/excluir bancos.

Revision ID: 0034_memory_banks
Revises: 0033_artifacts
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_memory_banks"
down_revision: Union[str, None] = "0033_artifacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_banks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_memory_banks_user_id", "memory_banks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_banks_user_id", table_name="memory_banks")
    op.drop_table("memory_banks")
