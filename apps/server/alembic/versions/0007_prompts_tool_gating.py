"""prompts + gating de ferramentas por modelo

Revision ID: 0007_prompts_tool_gating
Revises: 0006_tool_valves
Create Date: 2026-07-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_prompts_tool_gating"
down_revision: Union[str, None] = "0006_tool_valves"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("user_id", "command", name="uq_prompt_command"),
    )
    op.create_index("ix_prompts_user_id", "prompts", ["user_id"])

    op.add_column(
        "model_configs",
        sa.Column("tools_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "chats",
        sa.Column(
            "model_config_id",
            UUID,
            sa.ForeignKey("model_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("chats", "model_config_id")
    op.drop_column("model_configs", "tools_enabled")
    op.drop_index("ix_prompts_user_id", table_name="prompts")
    op.drop_table("prompts")
