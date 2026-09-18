"""User uploads stored outside the message (reference instead of inline base64).

Revision ID: 0075_uploads
Revises: 0074_media_jobs
Create Date: 2026-09-18
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0075_uploads"
down_revision: str | None = "0074_media_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.Uuid(),
                  sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("mime", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="file"),
        sa.Column("path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("attached", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_uploads_user_id", "uploads", ["user_id"])
    op.create_index("ix_uploads_chat_id", "uploads", ["chat_id"])
    op.create_index("ix_uploads_kind", "uploads", ["kind"])
    op.create_index("ix_uploads_attached", "uploads", ["attached"])


def downgrade() -> None:
    op.drop_table("uploads")
