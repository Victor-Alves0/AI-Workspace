"""schema inicial: users, secrets, folders, chats, messages, presets + extensão vector

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _ts(table: sa.Table | None = None) -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    # extensão usada pelo mem0 (pgvector)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_secrets",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.String(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_user_secret_name"),
    )
    op.create_index("ix_user_secrets_user_id", "user_secrets", ["user_id"])

    op.create_table(
        "folders",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "parent_id",
            UUID,
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_folders_user_id", "folders", ["user_id"])

    op.create_table(
        "chats",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "folder_id",
            UUID,
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=False, server_default="Novo Chat"),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "params", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_chats_user_id", "chats", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column(
            "chat_id",
            UUID,
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
    )
    op.create_index("ix_messages_chat_id", "messages", ["chat_id"])

    op.create_table(
        "presets",
        sa.Column("id", UUID, primary_key=True),
        *_ts(),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
    )
    op.create_index("ix_presets_user_id", "presets", ["user_id"])


def downgrade() -> None:
    op.drop_table("presets")
    op.drop_table("messages")
    op.drop_table("chats")
    op.drop_table("folders")
    op.drop_table("user_secrets")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
