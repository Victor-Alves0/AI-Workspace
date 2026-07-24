"""API pública: chaves por usuário + log de requisições.

`api_keys` guarda só prefixo (busca) + hash do segredo — a chave em claro existe
uma única vez, na resposta da criação. `api_requests` é o log por requisição
(inclui erros e 429, que não entram no ledger de uso).

`usage_events.api_key_id` amarra o gasto do ledger à chave que o originou, para o
painel responder "quanto esta aplicação consumiu" sem uma segunda contabilidade.

Revision ID: 0054_api_keys
Revises: 0053_codespace_default_model
Create Date: 2026-07-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0054_api_keys"
down_revision: Union[str, None] = "0053_codespace_default_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("scopes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("model_policy", JSONB(), nullable=False, server_default="{}"),
        sa.Column("limits", JSONB(), nullable=False, server_default="{}"),
        sa.Column("memory", JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip_allowlist", JSONB(), nullable=False, server_default="[]"),
        sa.Column("webhook", JSONB(), nullable=False, server_default="{}"),
        sa.Column("alerts_sent", JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])

    op.create_table(
        "api_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("key_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("endpoint", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("error", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_requests_user_id", "api_requests", ["user_id"])
    op.create_index("ix_api_requests_api_key_id", "api_requests", ["api_key_id"])
    op.create_index("ix_api_requests_created_at", "api_requests", ["created_at"])

    op.add_column("usage_events", sa.Column("api_key_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_events", "api_key_id")
    op.drop_index("ix_api_requests_created_at", table_name="api_requests")
    op.drop_index("ix_api_requests_api_key_id", table_name="api_requests")
    op.drop_index("ix_api_requests_user_id", table_name="api_requests")
    op.drop_table("api_requests")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
