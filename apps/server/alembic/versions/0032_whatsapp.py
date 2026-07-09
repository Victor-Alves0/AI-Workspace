"""Integração WhatsApp: conexões (números) + threads (conversas)

- whatsapp_connections: um número conectado por linha (provider "evolution" =
  não oficial via QR / "official" = Meta Cloud API), associado a um modelo,
  com filtros de contato/mensagem e política de memória (local | global).
- whatsapp_threads: mapeia cada conversa do WhatsApp (jid) a um Chat do app —
  o histórico fica visível na sidebar como um chat normal.

Revision ID: 0032_whatsapp
Revises: 0031_roundtable
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0032_whatsapp"
down_revision: Union[str, None] = "0031_roundtable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="evolution"),
        sa.Column("phone", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("instance", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("phone_number_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("access_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("app_secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("verify_token", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("webhook_token", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model_config_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("filters", JSONB(), nullable=False, server_default="{}"),
        sa.Column("memory", sa.String(length=8), nullable=False, server_default="local"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_config_id"], ["model_configs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_whatsapp_connections_user_id", "whatsapp_connections", ["user_id"])
    op.create_index(
        "ix_whatsapp_connections_webhook_token", "whatsapp_connections", ["webhook_token"]
    )

    op.create_table(
        "whatsapp_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("jid", sa.String(length=128), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("contact_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["whatsapp_connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_whatsapp_threads_connection_id", "whatsapp_threads", ["connection_id"])
    op.create_index(
        "ux_whatsapp_threads_conn_jid", "whatsapp_threads", ["connection_id", "jid"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_whatsapp_threads_conn_jid", table_name="whatsapp_threads")
    op.drop_index("ix_whatsapp_threads_connection_id", table_name="whatsapp_threads")
    op.drop_table("whatsapp_threads")
    op.drop_index("ix_whatsapp_connections_webhook_token", table_name="whatsapp_connections")
    op.drop_index("ix_whatsapp_connections_user_id", table_name="whatsapp_connections")
    op.drop_table("whatsapp_connections")
