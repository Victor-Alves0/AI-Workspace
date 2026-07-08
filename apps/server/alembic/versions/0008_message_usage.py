"""registro de uso (tokens/custo) por mensagem

Revision ID: 0008_message_usage
Revises: 0007_prompts_tool_gating
Create Date: 2026-07-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_message_usage"
down_revision: Union[str, None] = "0007_prompts_tool_gating"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("usage", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "usage")
