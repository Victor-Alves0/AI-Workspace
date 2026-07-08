"""skills + model_configs.skill_ids

Cria a tabela de skills (documentos carregados sob demanda via view_skill) e
adiciona a lista de skills equipadas por modelo personalizado.

Revision ID: 0017_skills
Revises: 0016_compaction_snapshot
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0017_skills"
down_revision: Union[str, None] = "0016_compaction_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "slug", name="uq_skill_slug"),
    )
    op.create_index("ix_skills_user_id", "skills", ["user_id"])
    op.add_column(
        "model_configs",
        sa.Column("skill_ids", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "skill_ids")
    op.drop_index("ix_skills_user_id", table_name="skills")
    op.drop_table("skills")
