"""Runtime durável: registro em disco dos comandos em background do Codespace

codespace_exec_jobs: espelha o ciclo de vida dos jobs longos só o suficiente p/ a
RECUPERAÇÃO no boot — achar os que ficaram devendo um desfecho (settled=False) após
um restart, marcá-los interrompidos e acordar o chat. Mata a perda silenciosa do
"wake que nunca chega".

Revision ID: 0067_exec_jobs_durable
Revises: 0066_health_events
Create Date: 2026-08-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067_exec_jobs_durable"
down_revision: Union[str, None] = "0066_health_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "codespace_exec_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_key", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("worktree", sa.String(length=64), nullable=True),
        sa.Column("command", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("settled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("output_tail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["codespace_projects.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_codespace_exec_jobs_job_key", "codespace_exec_jobs", ["job_key"], unique=True)
    op.create_index("ix_codespace_exec_jobs_user_id", "codespace_exec_jobs", ["user_id"])
    op.create_index("ix_codespace_exec_jobs_chat_id", "codespace_exec_jobs", ["chat_id"])
    op.create_index("ix_codespace_exec_jobs_status", "codespace_exec_jobs", ["status"])
    op.create_index("ix_codespace_exec_jobs_settled", "codespace_exec_jobs", ["settled"])


def downgrade() -> None:
    op.drop_table("codespace_exec_jobs")
