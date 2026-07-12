"""Second brain: cérebros de notas autorais da IA

- knowledge_bases: +kind ("kb" = RAG de documentos | "brain" = notas [[interligadas]]
  que a IA lê/escreve). Cérebros reusam as mesmas tabelas de pastas/docs/chunks.
- chats: +brain_config (JSONB nullable, herda do modelo/perfil):
  {"enabled": b, "brains": [<knowledge_base id>], "write": b, "k": int}

Revision ID: 0043_second_brain
Revises: 0042_knowledge_explorer_and_tags
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0043_second_brain"
down_revision: Union[str, None] = "0042_knowledge_explorer_and_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("kind", sa.String(length=8), nullable=False, server_default="kb"),
    )
    op.create_index("ix_knowledge_bases_user_kind", "knowledge_bases", ["user_id", "kind"])
    op.add_column("chats", sa.Column("brain_config", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "brain_config")
    op.drop_index("ix_knowledge_bases_user_kind", table_name="knowledge_bases")
    op.drop_column("knowledge_bases", "kind")
