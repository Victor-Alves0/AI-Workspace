"""Artefatos: conteúdos estruturados da IA numa janela dedicada (estilo Claude)

- artifacts: um artefato por (chat, identifier) — código/documento/HTML/etc.,
  sempre com o conteúdo da versão ATUAL.
- artifact_versions: histórico completo (visualizar/restaurar versões).

Revision ID: 0033_artifacts
Revises: 0032_whatsapp
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_artifacts"
down_revision: Union[str, None] = "0032_whatsapp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("identifier", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="text"),
        sa.Column("language", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_artifacts_chat_id", "artifacts", ["chat_id"])
    op.create_index("ux_artifacts_chat_identifier", "artifacts", ["chat_id", "identifier"], unique=True)

    op.create_table(
        "artifact_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=16), nullable=False, server_default="ai"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_artifact_versions_artifact_id", "artifact_versions", ["artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_artifact_versions_artifact_id", table_name="artifact_versions")
    op.drop_table("artifact_versions")
    op.drop_index("ux_artifacts_chat_identifier", table_name="artifacts")
    op.drop_index("ix_artifacts_chat_id", table_name="artifacts")
    op.drop_table("artifacts")
