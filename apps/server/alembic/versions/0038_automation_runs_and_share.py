"""Histórico de execuções de automações + compartilhamento de chat (link público)

- `automation_runs`: uma linha por disparo (agendado/manual), inclusive falhas.
- `chats.public_id`: token do link público read-only (/shared/<id>); NULL = privado.

Revision ID: 0038_automation_runs_and_share
Revises: 0037_knowledge_base
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_automation_runs_and_share"
down_revision: Union[str, None] = "0037_knowledge_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("automation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("trigger", sa.String(length=16), nullable=False, server_default="scheduled"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["automation_id"], ["automations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_automation_runs_automation_id", "automation_runs", ["automation_id"])
    op.create_index("ix_automation_runs_user_id", "automation_runs", ["user_id"])

    op.add_column("chats", sa.Column("public_id", sa.String(length=32), nullable=True))
    op.create_index("ix_chats_public_id", "chats", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_chats_public_id", table_name="chats")
    op.drop_column("chats", "public_id")
    op.drop_index("ix_automation_runs_user_id", table_name="automation_runs")
    op.drop_index("ix_automation_runs_automation_id", table_name="automation_runs")
    op.drop_table("automation_runs")
