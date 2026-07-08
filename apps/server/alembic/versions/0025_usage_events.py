"""ledger de uso (usage_events) + backfill das mensagens existentes

Uma linha por resposta do assistente (tokens/custo/modelo), gravada em paralelo à
mensagem. Sobrevive à exclusão do chat (referências sem FK). A Analítica passa a
ler daqui. O backfill copia o `usage` das mensagens de assistant já existentes.

Revision ID: 0025_usage_events
Revises: 0024_automation_options_chat_ttl
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_usage_events"
down_revision: Union[str, None] = "0024_automation_options_chat_ttl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("model_config_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("model_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="openrouter"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_user_created", "usage_events", ["user_id", "created_at"])

    # backfill: copia o `usage` (JSONB) das respostas de assistant já existentes.
    op.execute(
        """
        INSERT INTO usage_events (
            id, user_id, chat_id, message_id, model_config_id,
            model, model_name, provider,
            prompt_tokens, completion_tokens, total_tokens,
            reasoning_tokens, cached_tokens, cost,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            c.user_id,
            m.chat_id,
            m.id,
            NULLIF(m.usage->>'model_config_id', '')::uuid,
            COALESCE(m.usage->>'model', ''),
            COALESCE(m.usage->>'model_name', m.usage->>'model', ''),
            COALESCE(m.usage->>'provider', 'openrouter'),
            COALESCE((m.usage->>'prompt_tokens')::int, 0),
            COALESCE((m.usage->>'completion_tokens')::int, 0),
            COALESCE((m.usage->>'total_tokens')::int, 0),
            COALESCE((m.usage->>'reasoning_tokens')::int, 0),
            COALESCE((m.usage->>'cached_tokens')::int, 0),
            COALESCE((m.usage->>'cost')::double precision, 0),
            m.created_at,
            m.created_at
        FROM messages m
        JOIN chats c ON c.id = m.chat_id
        WHERE m.role = 'assistant'
          AND m.usage IS NOT NULL
          AND (
            COALESCE((m.usage->>'total_tokens')::int, 0) > 0
            OR COALESCE((m.usage->>'cost')::double precision, 0) > 0
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_user_created", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_table("usage_events")
