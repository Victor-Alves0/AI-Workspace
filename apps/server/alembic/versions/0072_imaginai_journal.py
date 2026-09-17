"""Add private player journal entries for Imaginai campaigns.

Revision ID: 0072_imaginai_journal
Revises: 0071_imaginai_world_kernel
Create Date: 2026-09-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0072_imaginai_journal"
down_revision: str | None = "0071_imaginai_world_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "imaginai_journal_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="Sem título"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["imaginai_campaigns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_imaginai_journal_entries_campaign_id",
        "imaginai_journal_entries",
        ["campaign_id"],
    )
    op.create_index(
        "ix_imaginai_journal_entries_user_id",
        "imaginai_journal_entries",
        ["user_id"],
    )
    op.create_index(
        "ix_imaginai_journal_entries_pinned",
        "imaginai_journal_entries",
        ["pinned"],
    )
    op.create_index(
        "ix_imaginai_journal_campaign_updated",
        "imaginai_journal_entries",
        ["campaign_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_table("imaginai_journal_entries")
