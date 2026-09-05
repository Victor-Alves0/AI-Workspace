"""Remote Terminal: máquinas remotas com agente instalado (VPS, servidor de casa)

remote_hosts: endereço + token do agente (cifrado), modo de TLS, o proxy que o
WORKSPACE usa para falar com a máquina (camada 1) e a política de saída que o AGENTE
aplica aos comandos (camada 2), cada uma com seu killswitch. Ver models/remote_host.py.

Revision ID: 0069_remote_hosts
Revises: 0068_model_config_unique_slug
Create Date: 2026-09-05
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069_remote_hosts"
down_revision: Union[str, None] = "0068_model_config_unique_slug"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remote_hosts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("base_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("token", sa.Text(), nullable=False, server_default=""),
        sa.Column("tls_mode", sa.String(length=8), nullable=False, server_default="pinned"),
        sa.Column("tls_cert_pem", sa.Text(), nullable=True),
        sa.Column("proxy_url", sa.Text(), nullable=True),
        sa.Column("require_proxy", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("egress", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("egress_proxy", sa.Text(), nullable=True),
        sa.Column("workdir", sa.Text(), nullable=False, server_default=""),
        sa.Column("shell", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("confirm_required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("info", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_remote_hosts_user_id", "remote_hosts", ["user_id"])
    op.create_index("ix_remote_hosts_slug", "remote_hosts", ["slug"])
    op.create_index("ix_remote_hosts_status", "remote_hosts", ["status"])
    # o modelo escolhe a máquina pelo slug: ele precisa ser único POR USUÁRIO
    op.create_unique_constraint("uq_remote_hosts_user_slug", "remote_hosts", ["user_id", "slug"])


def downgrade() -> None:
    op.drop_table("remote_hosts")
