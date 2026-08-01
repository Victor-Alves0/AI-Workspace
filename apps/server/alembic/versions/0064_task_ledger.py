"""Ledger de tarefa: memória de trabalho estruturada por chat

task_ledgers: um por chat (objetivo/status/plano/achados/notas/próximo passo). Injetado
no contexto a cada turno pra o agente convergir num objetivo multi-turno (dev/refactor/
security) sem re-derivar nem re-reportar achado refutado.

Revision ID: 0064_task_ledger
Revises: 0063_exec_enabled_default_on
Create Date: 2026-08-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_task_ledger"
down_revision: Union[str, None] = "0063_exec_enabled_default_on"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_ledgers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("plan", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("findings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("notes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("next_step", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["codespace_projects.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_task_ledgers_chat_id", "task_ledgers", ["chat_id"], unique=True)
    op.create_index("ix_task_ledgers_user_id", "task_ledgers", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_task_ledgers_user_id", table_name="task_ledgers")
    op.drop_index("ix_task_ledgers_chat_id", table_name="task_ledgers")
    op.drop_table("task_ledgers")
