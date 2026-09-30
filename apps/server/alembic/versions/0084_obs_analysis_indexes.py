"""Índices da análise de gargalos da Observabilidade: spans por período+nome, soma
dos filhos (tempo próprio) e a cadeia entre traces (`attrs.parent_trace`).

Revision ID: 0084_obs_analysis_indexes
Revises: 0083_subagent_jobs
Create Date: 2026-09-30
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0084_obs_analysis_indexes"
down_revision: str | None = "0083_subagent_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_obs_spans_started_name", "obs_spans", ["started_at", "name"])
    op.create_index("ix_obs_spans_parent", "obs_spans", ["parent_id"])
    # expressão: não vive no model (o autogenerate não compara índices de expressão)
    op.execute("CREATE INDEX IF NOT EXISTS ix_obs_traces_parent_trace "
               "ON obs_traces ((attrs->>'parent_trace'))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_obs_traces_parent_trace")
    op.drop_index("ix_obs_spans_parent", table_name="obs_spans")
    op.drop_index("ix_obs_spans_started_name", table_name="obs_spans")
