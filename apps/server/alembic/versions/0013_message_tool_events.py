"""messages: tool_events (usos de ferramenta embutidos no segmento)

Revision ID: 0013_message_tool_events
Revises: 0012_model_code_mode
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_message_tool_events"
down_revision: Union[str, None] = "0012_model_code_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("tool_events", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "tool_events")
