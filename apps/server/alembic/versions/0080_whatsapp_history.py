"""Histórico do WhatsApp embutido: conversas e mensagens do número.

O whatsmeow (motor embutido) não guarda histórico nem a lista de conversas; a
Evolution guardava no banco dela. Aqui o app guarda: o que chega, o que sai (pelo app
ou pelo celular) e o histórico que o WhatsApp manda ao parear.

Revision ID: 0080_whatsapp_history
Revises: 0079_native_memories
Create Date: 2026-09-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0080_whatsapp_history"
down_revision: str | None = "0079_native_memories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("connection_id", sa.Uuid(),
                  sa.ForeignKey("whatsapp_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jid", sa.String(length=128), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "whatsapp_chats",
        *_base(),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("connection_id", "jid", name="uq_whatsapp_chat_jid"),
    )
    op.create_index("ix_whatsapp_chats_connection_id", "whatsapp_chats", ["connection_id"])

    op.create_table(
        "whatsapp_messages",
        *_base(),
        sa.Column("msg_id", sa.String(length=128), nullable=False),
        sa.Column("from_me", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sender", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="text"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media", sa.LargeBinary(), nullable=True),
        sa.UniqueConstraint("connection_id", "jid", "msg_id", name="uq_whatsapp_message_id"),
    )
    op.create_index("ix_whatsapp_messages_conversa", "whatsapp_messages",
                    ["connection_id", "jid", "sent_at"])


def downgrade() -> None:
    op.drop_table("whatsapp_messages")
    op.drop_table("whatsapp_chats")
