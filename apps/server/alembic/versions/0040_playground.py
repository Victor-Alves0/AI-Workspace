"""Playground — benchmarks + histórico de execuções

- `benchmarks`: suíte de casos de teste (prompt + esperado/critério) + modelo-juiz.
- `benchmark_runs`: uma linha por disparo, com resultados por caso×modelo e agregados.

Comparações e Debug de Tools são efêmeros (não persistem).

Revision ID: 0040_playground
Revises: 0039_telegram_and_push
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0040_playground"
down_revision: Union[str, None] = "0039_telegram_and_push"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "benchmarks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("cases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("judge_model", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_benchmarks_user_id", "benchmarks", ["user_id"])

    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("benchmark_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("models", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("results", JSONB(), nullable=False, server_default="{}"),
        sa.Column("aggregates", JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["benchmark_id"], ["benchmarks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_benchmark_runs_benchmark_id", "benchmark_runs", ["benchmark_id"])
    op.create_index("ix_benchmark_runs_user_id", "benchmark_runs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_runs_user_id", table_name="benchmark_runs")
    op.drop_index("ix_benchmark_runs_benchmark_id", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
    op.drop_index("ix_benchmarks_user_id", table_name="benchmarks")
    op.drop_table("benchmarks")
