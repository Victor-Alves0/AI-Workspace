"""Canal Slack (Socket Mode): conexões + threads (mirror de conversas)

slack_channel_connections / slack_channel_threads: espelham o Discord, mas o
transporte é o Socket Mode (bot_token xoxb + app_token xapp). Distinto de
slack_accounts (a ferramenta slack.workspace.manage).

Revision ID: 0058_slack_channel
Revises: 0057_slack_accounts
Create Date: 2026-07-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_slack_channel"
down_revision: Union[str, None] = "0057_slack_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_channel_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("bot_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("app_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("team", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("bot_user_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("model_config_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("memory", sa.String(length=8), nullable=False, server_default="local"),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("humanize", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("debounce_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_window", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_config_id"], ["model_configs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_slack_channel_connections_user_id", "slack_channel_connections", ["user_id"])

    op.create_table(
        "slack_channel_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("is_dm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["slack_channel_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_slack_channel_threads_connection_id", "slack_channel_threads", ["connection_id"])
    op.create_index("ix_slack_channel_threads_channel_id", "slack_channel_threads", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_slack_channel_threads_channel_id", table_name="slack_channel_threads")
    op.drop_index("ix_slack_channel_threads_connection_id", table_name="slack_channel_threads")
    op.drop_table("slack_channel_threads")
    op.drop_index("ix_slack_channel_connections_user_id", table_name="slack_channel_connections")
    op.drop_table("slack_channel_connections")
