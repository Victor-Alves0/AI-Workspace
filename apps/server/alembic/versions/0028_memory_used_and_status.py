"""memórias usadas por turno + status do flag (disabled/pending)

- messages.memories_used (JSONB): quais memórias foram injetadas naquela resposta
  [{id, text, scope}] — mostrado na UI com desativar/excluir inline.
- disabled_memories.status (str): 'disabled' (soft-off manual) ou 'pending'
  (aguardando revisão antes de o modelo usar). Ambos = ocultos da recuperação.

Revision ID: 0028_memory_used_and_status
Revises: 0027_disabled_memories
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0028_memory_used_and_status"
down_revision: Union[str, None] = "0027_disabled_memories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("memories_used", JSONB(), nullable=True))
    op.add_column(
        "disabled_memories",
        sa.Column("status", sa.String(16), nullable=False, server_default="disabled"),
    )


def downgrade() -> None:
    op.drop_column("disabled_memories", "status")
    op.drop_column("messages", "memories_used")
