"""memórias desativadas (soft-off): tabela disabled_memories

Uma memória "desativada" continua guardada no mem0, mas é EXCLUÍDA da recuperação
por turno (o modelo não a vê) até ser reativada. Guardamos só o id do mem0 por
usuário; gerenciado via psycopg2 dentro do mem0_service.

Revision ID: 0027_disabled_memories
Revises: 0026_chat_memory_config
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_disabled_memories"
down_revision: Union[str, None] = "0026_chat_memory_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "disabled_memories",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_id", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "memory_id"),
    )


def downgrade() -> None:
    op.drop_table("disabled_memories")
