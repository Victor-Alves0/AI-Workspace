"""Integração Notion (tool: contas conectadas por token/OAuth)

notion_accounts: contas Notion por-usuário (token de integração interna OU bot token
OAuth). O token do Notion não expira nem tem refresh, então não há colunas de refresh
ou expiração (diferente de github_accounts).

Revision ID: 0056_notion_accounts
Revises: 0055_observability_traces
Create Date: 2026-07-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_notion_accounts"
down_revision: Union[str, None] = "0055_observability_traces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notion_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("bot_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("token", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=8), nullable=False, server_default="token"),
        sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_notion_accounts_user_id", "notion_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notion_accounts_user_id", table_name="notion_accounts")
    op.drop_table("notion_accounts")
