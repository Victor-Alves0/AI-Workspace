"""WhatsApp: atender ou não, compactação automática e memória como a dos modelos.

- `auto_reply`: False = o número só fica disponível para a IA do chat (ler, ver o
  que chegou, enviar pela ferramenta de mensagens); ninguém responde sozinho.
- `compaction`: resume as conversas longas antes do turno, como nos chats.
- `memory_config`: {enabled, write, read:{global,model,chat}, banks} — o mesmo
  formato da memória do modelo. NULL = segue a coluna antiga `memory`.

Revision ID: 0089_whatsapp_reply_memory
Revises: 0088_cyberlab_case
Create Date: 2026-10-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0089_whatsapp_reply_memory"
down_revision: str | None = "0088_cyberlab_case"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("whatsapp_connections",
                  sa.Column("auto_reply", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("whatsapp_connections",
                  sa.Column("compaction", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("whatsapp_connections",
                  sa.Column("memory_config", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("whatsapp_connections", "memory_config")
    op.drop_column("whatsapp_connections", "compaction")
    op.drop_column("whatsapp_connections", "auto_reply")
