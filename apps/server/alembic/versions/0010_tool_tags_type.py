"""tools: tags (organização), tool_type (code|mcp) e mcp_config

Revision ID: 0010_tool_tags_type
Revises: 0009_user_profile
Create Date: 2026-07-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_tool_tags_type"
down_revision: Union[str, None] = "0009_user_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tools",
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "tools",
        sa.Column("tool_type", sa.String(20), nullable=False, server_default="code"),
    )
    op.add_column(
        "tools",
        sa.Column("mcp_config", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("tools", "mcp_config")
    op.drop_column("tools", "tool_type")
    op.drop_column("tools", "tags")
