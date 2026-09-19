"""Chat-level Mini App identity and Imaginai session-zero stage.

Revision ID: 0078_chat_mini_app
Revises: 0077_sound_effects
Create Date: 2026-09-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0078_chat_mini_app"
down_revision: str | None = "0077_sound_effects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("mini_app", sa.String(length=32), nullable=True))
    # chats que já são mesas de RPG ganham o selo
    op.execute(
        "UPDATE chats SET mini_app = 'imaginai' "
        "WHERE id IN (SELECT chat_id FROM imaginai_campaigns)"
    )
    # campanhas existentes já estão em jogo: não voltam para a sessão zero
    op.add_column(
        "imaginai_campaigns",
        sa.Column("setup_stage", sa.String(length=16), nullable=False, server_default="play"),
    )


def downgrade() -> None:
    op.drop_column("imaginai_campaigns", "setup_stage")
    op.drop_column("chats", "mini_app")
