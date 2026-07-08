"""chat_compactions.snapshot — snapshot das mensagens p/ restaurar o checkpoint

Guarda a lista completa de mensagens que existiam antes da compactação, para que
FIXAR/RESTAURAR um checkpoint traga a conversa exatamente àquele ponto.

Revision ID: 0016_compaction_snapshot
Revises: 0015_chat_compactions
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0016_compaction_snapshot"
down_revision: Union[str, None] = "0015_chat_compactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_compactions",
        sa.Column("snapshot", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("chat_compactions", "snapshot")
