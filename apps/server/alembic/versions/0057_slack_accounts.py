"""Integração Slack (tool: workspaces conectados por token/OAuth)

slack_accounts: workspaces Slack por-usuário para a ferramenta slack.workspace.manage
(bot/user token OU OAuth). Token não expira → sem colunas de refresh/expiração.
(O CANAL Slack — mirror de conversas — virá com suas próprias tabelas.)

Revision ID: 0057_slack_accounts
Revises: 0056_notion_accounts
Create Date: 2026-07-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057_slack_accounts"
down_revision: Union[str, None] = "0056_notion_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slack_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("team_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("bot_user_id", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("token", sa.Text(), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=8), nullable=False, server_default="token"),
        sa.Column("avatar_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_slack_accounts_user_id", "slack_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_slack_accounts_user_id", table_name="slack_accounts")
    op.drop_table("slack_accounts")
