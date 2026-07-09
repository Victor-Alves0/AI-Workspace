"""Mesa-redonda (multi-model chat): modelos conversam entre si

- chats.mode: "single" (padrão) | "roundtable"
- chats.participants: lista de participantes (modelo base ou ModelConfig custom,
  nome, cor, avatar, persona)
- chats.roundtable_config: política de turno (round_robin|manual|moderator),
  moderador, teto de rodadas e quem fala em seguida
- messages.speaker: quem produziu a fala (participante), p/ a bolha rotulada

Revision ID: 0031_roundtable
Revises: 0030_model_config_slug
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0031_roundtable"
down_revision: Union[str, None] = "0030_model_config_slug"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("mode", sa.String(16), nullable=False, server_default="single"),
    )
    op.add_column(
        "chats",
        sa.Column("participants", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("chats", sa.Column("roundtable_config", JSONB(), nullable=True))
    op.add_column("messages", sa.Column("speaker", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "speaker")
    op.drop_column("chats", "roundtable_config")
    op.drop_column("chats", "participants")
    op.drop_column("chats", "mode")
