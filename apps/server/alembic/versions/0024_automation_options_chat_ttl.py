"""opções por-automação (contexto/duração do chat) + TTL de chats

- automations.options: {"use_context": bool, "chat_ttl": horas | "view_once"}
- chats.expires_at / chats.view_once: "Duração do Chat" — o scheduler apaga chats
  vencidos; view_once é apagado pelo front quando o usuário abre e sai.

Revision ID: 0024_automation_options_chat_ttl
Revises: 0023_generated_images
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0024_automation_options_chat_ttl"
down_revision: Union[str, None] = "0023_generated_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automations",
        sa.Column("options", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "chats",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chats",
        sa.Column("view_once", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_chats_expires_at", "chats", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_chats_expires_at", table_name="chats")
    op.drop_column("chats", "view_once")
    op.drop_column("chats", "expires_at")
    op.drop_column("automations", "options")
