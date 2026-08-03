"""Auto-observabilidade: eventos de saúde das capacidades do harness

health_events: um registro estruturado toda vez que uma capacidade central degrada
ou faz fallback (mem0 no-op, síntese caindo p/ camada C, watchdog abortando tool,
deadline do codegraph, etc.) — o que antes só virava um `warning` solto. Substrato
da medição de primitivos e dos alarmes de auto-observabilidade.

Revision ID: 0066_health_events
Revises: 0065_investigation_graph
Create Date: 2026-08-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_health_events"
down_revision: Union[str, None] = "0065_investigation_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "health_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("capability", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("event", sa.String(length=48), nullable=False, server_default=""),
        sa.Column("severity", sa.String(length=12), nullable=False, server_default="info"),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_health_events_capability", "health_events", ["capability"])
    op.create_index("ix_health_events_severity", "health_events", ["severity"])
    op.create_index("ix_health_events_cap_created", "health_events", ["capability", "created_at"])
    op.create_index("ix_health_events_sev_created", "health_events", ["severity", "created_at"])


def downgrade() -> None:
    op.drop_table("health_events")
