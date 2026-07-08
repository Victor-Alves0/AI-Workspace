"""model_configs: code_mode (run_code da SIFT, opcional por modelo)

Revision ID: 0012_model_code_mode
Revises: 0011_message_reasoning
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_model_code_mode"
down_revision: Union[str, None] = "0011_message_reasoning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column("code_mode", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "code_mode")
