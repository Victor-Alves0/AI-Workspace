"""CyberLab: caso de avaliação de segurança guiada (IA conduz o operador humano).

Um caso por chat (apagar o chat apaga o caso). `mode` escolhe o protocolo do turno
(blackbox primeiro); `authorization` registra o alvo autorizado (base do relatório e
dos guard-rails futuros); `settings` guarda o pipeline (log de passos + achados).

Revision ID: 0088_cyberlab_case
Revises: 0087_google_account_order
Create Date: 2026-10-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0088_cyberlab_case"
down_revision: str | None = "0087_google_account_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cyberlab_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default="blackbox"),
        sa.Column("target", sa.Text(), nullable=False, server_default=""),
        sa.Column("authorization", sa.Text(), nullable=False, server_default=""),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("phase", sa.String(32), nullable=False, server_default="scoping"),
        sa.Column(
            "settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("chat_id", name="uq_cyberlab_case_chat"),
    )
    op.create_index("ix_cyberlab_cases_user_id", "cyberlab_cases", ["user_id"])
    op.create_index("ix_cyberlab_cases_chat_id", "cyberlab_cases", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_cyberlab_cases_chat_id", table_name="cyberlab_cases")
    op.drop_index("ix_cyberlab_cases_user_id", table_name="cyberlab_cases")
    op.drop_table("cyberlab_cases")
