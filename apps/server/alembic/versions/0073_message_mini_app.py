"""Persist the Mini App that explicitly owns a user message.

Revision ID: 0073_message_mini_app
Revises: 0072_imaginai_journal
Create Date: 2026-09-17
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0073_message_mini_app"
down_revision: str | None = "0072_imaginai_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("mini_app", sa.String(length=32), nullable=True))
    op.create_index("ix_messages_mini_app", "messages", ["mini_app"])


def downgrade() -> None:
    op.drop_index("ix_messages_mini_app", table_name="messages")
    op.drop_column("messages", "mini_app")
