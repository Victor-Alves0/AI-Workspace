"""Ledger de uso: detalhamento de tokens por ferramenta (`tools_breakdown`)

Persiste no `usage_events` o mapa {caminho_da_tool: tokens} já calculado por
resposta (orchestrator), para alimentar a aba "Por modelo" da Analítica — que
mostra o gasto por ferramenta e sobrevive à exclusão do chat.

Revision ID: 0041_usage_tools_breakdown
Revises: 0040_playground
Create Date: 2026-07-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0041_usage_tools_breakdown"
down_revision: Union[str, None] = "0040_playground"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usage_events",
        sa.Column("tools_breakdown", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("usage_events", "tools_breakdown")
