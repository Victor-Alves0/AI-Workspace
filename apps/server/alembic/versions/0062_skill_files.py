"""Skills: arquivos de referência (`files`)

Uma Skill deixa de ser só o SKILL.md e passa a carregar arquivos de referência
(references/*, snippets, tabelas) — como as skills "de pasta" do Claude/Anthropic.
Guardados como JSONB `[{"name": "references/x.md", "content": "…"}]`. O modelo os
carrega sob demanda via `view_skill(slug, file=...)`. `skill_proposals` ganha a
mesma coluna para que o import de uma pasta traga os auxiliares junto na proposta.

Revision ID: 0062_skill_files
Revises: 0061_knowledge_enrichment
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0062_skill_files"
down_revision: Union[str, None] = "0061_knowledge_enrichment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("files", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "skill_proposals",
        sa.Column("files", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("skill_proposals", "files")
    op.drop_column("skills", "files")
