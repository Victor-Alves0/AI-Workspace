"""messages: reasoning ("thinking" do modelo, com duração)

Revision ID: 0011_message_reasoning
Revises: 0010_tool_tags_type
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_message_reasoning"
down_revision: Union[str, None] = "0010_tool_tags_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("reasoning", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "reasoning")
