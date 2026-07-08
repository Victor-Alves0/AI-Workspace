"""adiciona users.default_model

Revision ID: 0004_user_default_model
Revises: 0003_chat_pinned
Create Date: 2026-06-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_user_default_model"
down_revision: Union[str, None] = "0003_chat_pinned"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("default_model", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "default_model")
