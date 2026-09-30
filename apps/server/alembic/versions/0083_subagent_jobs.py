"""Agentes em segundo plano duráveis (retomados após um restart).

Revision ID: 0083_subagent_jobs
Revises: 0082_sync
Create Date: 2026-09-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0083_subagent_jobs"
down_revision: str | None = "0082_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subagent_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_key", sa.String(40), nullable=False),
        sa.Column("chat_id", sa.Uuid(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("task", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("results", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("card", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_subagent_jobs_job_key", "subagent_jobs", ["job_key"], unique=True)
    op.create_index("ix_subagent_jobs_chat_id", "subagent_jobs", ["chat_id"])


def downgrade() -> None:
    op.drop_table("subagent_jobs")
