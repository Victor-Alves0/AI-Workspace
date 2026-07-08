"""memória por-chat: coluna chats.memory_config

Config de memória efetiva do chat (JSONB, nullable = herda do modelo/perfil):
  {"write": "global|model|chat|off", "read": {"global": bool, "model": bool, "chat": bool}}
A leitura é a UNIÃO dos escopos ligados; a escrita vai para UM escopo.

Revision ID: 0026_chat_memory_config
Revises: 0025_usage_events
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0026_chat_memory_config"
down_revision: Union[str, None] = "0025_usage_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("memory_config", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "memory_config")
