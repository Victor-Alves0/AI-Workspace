"""Codespace — projetos (repositório clonado + índice de grafo de código).

Slice 1: só leitura, origem via `git clone` HTTPS. `chats.project_id` vincula um
chat a um projeto (habilita as tools code.graph.query/code.files.browse).

Revision ID: 0050_codespace_projects
Revises: 0049_skill_proposals
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0050_codespace_projects"
down_revision: Union[str, None] = "0049_skill_proposals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "codespace_projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="git"),
        sa.Column("repo_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("branch", sa.String(length=120), nullable=False, server_default="main"),
        sa.Column("github_account_id", sa.Uuid(), nullable=True),
        sa.Column("scope", JSONB(), nullable=False, server_default="{}"),
        sa.Column("index_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("stats", JSONB(), nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["github_account_id"], ["github_accounts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_codespace_projects_user_id", "codespace_projects", ["user_id"])
    op.create_index("ix_codespace_projects_index_status", "codespace_projects", ["index_status"])

    op.add_column("chats", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_chats_project_id", "chats", "codespace_projects", ["project_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_chats_project_id", "chats", type_="foreignkey")
    op.drop_column("chats", "project_id")
    op.drop_index("ix_codespace_projects_index_status", table_name="codespace_projects")
    op.drop_index("ix_codespace_projects_user_id", table_name="codespace_projects")
    op.drop_table("codespace_projects")
