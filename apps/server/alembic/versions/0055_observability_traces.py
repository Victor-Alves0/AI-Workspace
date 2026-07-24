"""Observabilidade: traces (operações) + spans (etapas).

Rastro fim-a-fim persistido para diagnóstico fino: latência, tempo de banco,
leituras/escritas e tempo de conexão de cada chamada. Metadados, não conteúdo.

`obs_traces.user_id` cascateia (apagar a conta apaga o rastro dela); `obs_spans`
cascateia do trace. Índices por (kind|user|status, started_at) — os recortes que a
tela de consulta usa; e por trace_id para montar o waterfall.

Revision ID: 0055_observability_traces
Revises: 0054_api_keys
Create Date: 2026-07-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0055_observability_traces"
down_revision: Union[str, None] = "0054_api_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "obs_traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="http"),
        sa.Column("method", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("path", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=8), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("span_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("db_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("db_queries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("llm_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attrs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_obs_traces_started_at", "obs_traces", ["started_at"])
    op.create_index("ix_obs_traces_kind_started", "obs_traces", ["kind", "started_at"])
    op.create_index("ix_obs_traces_user_started", "obs_traces", ["user_id", "started_at"])
    op.create_index("ix_obs_traces_status_started", "obs_traces", ["status", "started_at"])

    op.create_table(
        "obs_spans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="internal"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offset_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=8), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("db_reads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("db_writes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("db_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("http_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attrs", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trace_id"], ["obs_traces.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_obs_spans_trace_id", "obs_spans", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_obs_spans_trace_id", table_name="obs_spans")
    op.drop_table("obs_spans")
    op.drop_index("ix_obs_traces_status_started", table_name="obs_traces")
    op.drop_index("ix_obs_traces_user_started", table_name="obs_traces")
    op.drop_index("ix_obs_traces_kind_started", table_name="obs_traces")
    op.drop_index("ix_obs_traces_started_at", table_name="obs_traces")
    op.drop_table("obs_traces")
