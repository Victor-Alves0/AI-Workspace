"""Codespace: tarefas (worktrees) + campos de execução/pasta-local no projeto

codespace_tasks: um worktree git isolado (branch própria) por tarefa de agente, com
status running→awaiting_review→merged/discarded/error e resumo do diff. Novos campos
em codespace_projects: setup_command/test_command/exec_enabled (sandbox de execução) e
local_path (source="folder", abrir diretório existente no host).

Revision ID: 0059_codespace_tasks
Revises: 0058_slack_channel
Create Date: 2026-07-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059_codespace_tasks"
down_revision: Union[str, None] = "0058_slack_channel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "codespace_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("agent", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("base_branch", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("worktree_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("diff_stat", postgresql.JSONB(), nullable=True),
        sa.Column("test_status", sa.String(length=16), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["codespace_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_codespace_tasks_project_id", "codespace_tasks", ["project_id"])
    op.create_index("ix_codespace_tasks_user_id", "codespace_tasks", ["user_id"])
    op.create_index("ix_codespace_tasks_status", "codespace_tasks", ["status"])

    op.add_column("codespace_projects", sa.Column("local_path", sa.Text(), nullable=True))
    op.add_column("codespace_projects", sa.Column("setup_command", sa.Text(), nullable=False, server_default=""))
    op.add_column("codespace_projects", sa.Column("test_command", sa.Text(), nullable=False, server_default=""))
    op.add_column("codespace_projects", sa.Column("exec_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("codespace_projects", "exec_enabled")
    op.drop_column("codespace_projects", "test_command")
    op.drop_column("codespace_projects", "setup_command")
    op.drop_column("codespace_projects", "local_path")
    op.drop_index("ix_codespace_tasks_status", table_name="codespace_tasks")
    op.drop_index("ix_codespace_tasks_user_id", table_name="codespace_tasks")
    op.drop_index("ix_codespace_tasks_project_id", table_name="codespace_tasks")
    op.drop_table("codespace_tasks")
