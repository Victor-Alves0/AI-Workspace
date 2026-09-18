"""Turn-based combat state on the Imaginai campaign.

Revision ID: 0076_imaginai_encounter
Revises: 0075_uploads
Create Date: 2026-09-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0076_imaginai_encounter"
down_revision: str | None = "0075_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "imaginai_campaigns",
        sa.Column("encounter", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("imaginai_campaigns", "encounter")
