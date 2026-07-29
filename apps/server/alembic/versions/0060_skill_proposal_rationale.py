"""Proposta de skill: coluna `rationale` (por que a IA sugeriu isto)

O Curator passa a explicar o motivo de cada proposta (ex.: "o usuário corrigiu o
fluxo X duas vezes"). Mostrado na UI de Sugestões para o usuário entender a origem.

Revision ID: 0060_skill_proposal_rationale
Revises: 0059_codespace_tasks
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0060_skill_proposal_rationale"
down_revision: Union[str, None] = "0059_codespace_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "skill_proposals",
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("skill_proposals", "rationale")
