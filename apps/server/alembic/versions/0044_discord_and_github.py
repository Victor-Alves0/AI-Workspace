"""Integração Discord (canal: conexões + threads) + GitHub (tool: contas conectadas)

- discord_connections / discord_threads: espelham o Telegram, mas via Gateway (WS);
  a conexão guarda o estado de RESUME em `state` (sem update_offset).
- github_accounts: contas GitHub por-usuário (PAT ou OAuth), token cifrado.

Revision ID: 0044_discord_and_github
Revises: 0043_second_brain
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_discord_and_github"
down_revision: Union[str, None] = "0043_second_brain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discord_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("bot_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("bot_username", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("app_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("model_config_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("memory", sa.String(length=8), nullable=False, server_default="local"),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("humanize", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_config_id"], ["model_configs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_discord_connections_user_id", "discord_connections", ["user_id"])

    op.create_table(
        "discord_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("is_dm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["discord_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_discord_threads_connection_id", "discord_threads", ["connection_id"])
    op.create_index("ix_discord_threads_channel_id", "discord_threads", ["channel_id"])

    op.create_table(
        "github_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("login", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("token", sa.Text(), nullable=False, server_default=""),
        sa.Column("refresh_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=8), nullable=False, server_default="pat"),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_github_accounts_user_id", "github_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_github_accounts_user_id", table_name="github_accounts")
    op.drop_table("github_accounts")
    op.drop_index("ix_discord_threads_channel_id", table_name="discord_threads")
    op.drop_index("ix_discord_threads_connection_id", table_name="discord_threads")
    op.drop_table("discord_threads")
    op.drop_index("ix_discord_connections_user_id", table_name="discord_connections")
    op.drop_table("discord_connections")
