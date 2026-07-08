"""google accounts (integração Gmail + Agenda)

Cria a tabela de contas Google conectadas por usuário (multi-conta). Só o refresh
token é guardado (cifrado); as credenciais do app OAuth ficam em app_settings.

Revision ID: 0022_google_accounts
Revises: 0021_automations
Create Date: 2026-07-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_google_accounts"
down_revision: Union[str, None] = "0021_automations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "google_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("refresh_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_google_accounts_user_id", "google_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_google_accounts_user_id", table_name="google_accounts")
    op.drop_table("google_accounts")
