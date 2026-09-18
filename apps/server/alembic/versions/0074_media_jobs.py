"""Durable queue for media generations that finish after the turn.

Revision ID: 0074_media_jobs
Revises: 0073_message_mini_app
Create Date: 2026-09-17
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0074_media_jobs"
down_revision: str | None = "0073_message_mini_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="civitai"),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.Uuid(),
                  sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("settled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_media_jobs_provider", "media_jobs", ["provider"])
    op.create_index("ix_media_jobs_external_id", "media_jobs", ["external_id"])
    op.create_index("ix_media_jobs_user_id", "media_jobs", ["user_id"])
    op.create_index("ix_media_jobs_chat_id", "media_jobs", ["chat_id"])
    op.create_index("ix_media_jobs_settled", "media_jobs", ["settled"])


def downgrade() -> None:
    op.drop_table("media_jobs")
