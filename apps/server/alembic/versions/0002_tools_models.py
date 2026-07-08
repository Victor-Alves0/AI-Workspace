"""tools (ferramentas do usuário) + model_configs (modelos personalizados)

Revision ID: 0002_tools_models
Revises: 0001_initial
Create Date: 2026-06-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_tools_models"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("returns", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("code", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("user_id", "path", name="uq_tool_path"),
    )
    op.create_index("ix_tools_user_id", "tools", ["user_id"])

    op.create_table(
        "model_configs",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_model", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("tool_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("prompt_suggestions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tts_voice", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_model_configs_user_id", "model_configs", ["user_id"])


def downgrade() -> None:
    op.drop_table("model_configs")
    op.drop_table("tools")
