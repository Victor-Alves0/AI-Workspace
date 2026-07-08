"""model_configs: sift_config (como o SIFT é usado no modelo)

Guarda {"mode": "prompt"|"list", "prompt": <quando-usar>, "pinned": [tool_id...]}.

Revision ID: 0014_model_sift_config
Revises: 0013_message_tool_events
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014_model_sift_config"
down_revision: Union[str, None] = "0013_message_tool_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column("sift_config", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "sift_config")
