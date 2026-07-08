"""chat_compactions: parent_id + name (grafo/árvore de checkpoints)

Revision ID: 0020_compaction_graph
Revises: 0019_message_attachments
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_compaction_graph"
down_revision: Union[str, None] = "0019_message_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_compactions", sa.Column("parent_id", sa.Uuid(), nullable=True))
    op.add_column("chat_compactions", sa.Column("name", sa.String(length=120), nullable=True))
    op.create_foreign_key(
        "fk_compaction_parent",
        "chat_compactions",
        "chat_compactions",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_compaction_parent", "chat_compactions", type_="foreignkey")
    op.drop_column("chat_compactions", "name")
    op.drop_column("chat_compactions", "parent_id")
