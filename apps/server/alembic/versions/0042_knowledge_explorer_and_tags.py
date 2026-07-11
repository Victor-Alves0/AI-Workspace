"""Explorador de conhecimento (pastas + metadados) e tags em bases/chats

- knowledge_folders: pastas aninháveis dentro de uma base (self-FK parent_id)
- knowledge_docs: +folder_id (SET NULL) e +meta (JSONB: title/description/tags)
- knowledge_bases: +tags (JSONB lista)
- chats: +tags (JSONB lista)

Revision ID: 0042_knowledge_explorer_and_tags
Revises: 0041_usage_tools_breakdown
Create Date: 2026-07-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0042_knowledge_explorer_and_tags"
down_revision: Union[str, None] = "0041_usage_tools_breakdown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_folders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("base_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["knowledge_folders.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_knowledge_folders_base_id", "knowledge_folders", ["base_id"])
    op.create_index("ix_knowledge_folders_user_id", "knowledge_folders", ["user_id"])

    op.add_column("knowledge_docs", sa.Column("folder_id", sa.Uuid(), nullable=True))
    op.add_column("knowledge_docs", sa.Column("meta", JSONB(), nullable=True))
    op.create_index("ix_knowledge_docs_folder_id", "knowledge_docs", ["folder_id"])
    op.create_foreign_key(
        "fk_knowledge_docs_folder_id", "knowledge_docs", "knowledge_folders",
        ["folder_id"], ["id"], ondelete="SET NULL",
    )

    op.add_column(
        "knowledge_bases",
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "chats",
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("chats", "tags")
    op.drop_column("knowledge_bases", "tags")
    op.drop_constraint("fk_knowledge_docs_folder_id", "knowledge_docs", type_="foreignkey")
    op.drop_index("ix_knowledge_docs_folder_id", table_name="knowledge_docs")
    op.drop_column("knowledge_docs", "meta")
    op.drop_column("knowledge_docs", "folder_id")
    op.drop_index("ix_knowledge_folders_user_id", table_name="knowledge_folders")
    op.drop_index("ix_knowledge_folders_base_id", table_name="knowledge_folders")
    op.drop_table("knowledge_folders")
