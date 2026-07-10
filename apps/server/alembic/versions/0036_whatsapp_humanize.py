"""WhatsApp: Modo humanizador (digitação simulada + quebra de mensagens)

Coluna `humanize` (JSONB) em whatsapp_connections:
  {enabled, typing, min_seconds, max_seconds, split}

Revision ID: 0036_whatsapp_humanize
Revises: 0035_whatsapp_prompt_limits
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036_whatsapp_humanize"
down_revision: Union[str, None] = "0035_whatsapp_prompt_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_connections",
        sa.Column("humanize", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_connections", "humanize")
