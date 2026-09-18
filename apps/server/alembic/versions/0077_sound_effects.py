"""Cache of generated sound effects (one generation per description per user).

Revision ID: 0077_sound_effects
Revises: 0076_imaginai_encounter
Create Date: 2026-09-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0077_sound_effects"
down_revision: str | None = "0076_imaginai_encounter"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sound_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_id", sa.Uuid(),
                  sa.ForeignKey("generated_images.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_sound_effect_user_key"),
    )
    op.create_index("ix_sound_effects_user_id", "sound_effects", ["user_id"])
    op.create_index("ix_sound_effects_key", "sound_effects", ["key"])


def downgrade() -> None:
    op.drop_table("sound_effects")
