"""adiciona chats.pinned

Revision ID: 0003_chat_pinned
Revises: 0002_tools_models
Create Date: 2026-06-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_chat_pinned"
down_revision: Union[str, None] = "0002_tools_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("chats", "pinned")
