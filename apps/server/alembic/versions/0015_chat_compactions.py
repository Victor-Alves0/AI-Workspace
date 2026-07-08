"""checkpoints de compactação + messages.is_summary

Guarda o histórico de compactações (timeline) e permite fixar (pin) qual resumo
é o contexto ativo. messages.is_summary marca a mensagem-resumo no topo do chat.

Revision ID: 0015_chat_compactions
Revises: 0014_model_sift_config
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_chat_compactions"
down_revision: Union[str, None] = "0014_model_sift_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("is_summary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "chat_compactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chat_compactions_chat_id", "chat_compactions", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_compactions_chat_id", table_name="chat_compactions")
    op.drop_table("chat_compactions")
    op.drop_column("messages", "is_summary")
