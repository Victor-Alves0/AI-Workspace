"""Integração Telegram (conexões + threads) + Web Push (inscrições do navegador)

Revision ID: 0039_telegram_and_push
Revises: 0038_automation_runs_and_share
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_telegram_and_push"
down_revision: Union[str, None] = "0038_automation_runs_and_share"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("bot_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("bot_username", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model_config_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("memory", sa.String(length=8), nullable=False, server_default="local"),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("humanize", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("update_offset", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_config_id"], ["model_configs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_telegram_connections_user_id", "telegram_connections", ["user_id"])

    op.create_table(
        "telegram_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("tg_chat_id", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["telegram_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_telegram_threads_connection_id", "telegram_threads", ["connection_id"])
    op.create_index("ix_telegram_threads_tg_chat_id", "telegram_threads", ["tg_chat_id"])

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("auth", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("ua", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_table("push_subscriptions")
    op.drop_index("ix_telegram_threads_tg_chat_id", table_name="telegram_threads")
    op.drop_index("ix_telegram_threads_connection_id", table_name="telegram_threads")
    op.drop_table("telegram_threads")
    op.drop_index("ix_telegram_connections_user_id", table_name="telegram_connections")
    op.drop_table("telegram_connections")
