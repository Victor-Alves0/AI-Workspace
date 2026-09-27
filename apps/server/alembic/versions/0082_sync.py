"""Sincronização entre instâncias (servidor ↔ desktop ↔ …).

- `sync_peers`: as instâncias pareadas (segredo do par cifrado, cursores, mapa de
  usuários por e-mail).
- `sync_rows` + `sync_seq`: a versão de cada linha replicada (uma entrada por linha:
  última mudança, quando, de qual instância, se foi apagada). É preenchida por
  gatilhos que só são instalados quando a primeira instância é pareada — quem não
  usa sync não paga nada por escrita.
- `sync_pending`: mudanças recebidas que ainda não puderam ser aplicadas (o pai da
  linha ainda não chegou); tentadas de novo a cada ciclo.

Revision ID: 0082_sync
Revises: 0081_voice_local_to_builtin
Create Date: 2026-09-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0082_sync"
down_revision: str | None = "0081_voice_local_to_builtin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_peers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("url", sa.String(512), nullable=True),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_map", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("push_cursor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("pull_cursor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("remote_schema", sa.String(64), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("CREATE SEQUENCE IF NOT EXISTS sync_seq")
    op.create_table(
        "sync_rows",
        sa.Column("tbl", sa.String(64), primary_key=True),
        sa.Column("row_id", sa.Uuid(), primary_key=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.Uuid(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_sync_rows_seq", "sync_rows", ["seq"])
    op.create_table(
        "sync_pending",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("peer_id", sa.Uuid(), sa.ForeignKey("sync_peers.id", ondelete="CASCADE"), index=True),
        sa.Column("change", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    # os gatilhos de captura (instalados em tempo de execução) saem junto com a função
    op.execute("DROP FUNCTION IF EXISTS aiw_sync_capture() CASCADE")
    op.drop_table("sync_pending")
    op.drop_index("ix_sync_rows_seq", table_name="sync_rows")
    op.drop_table("sync_rows")
    op.execute("DROP SEQUENCE IF EXISTS sync_seq")
    op.drop_table("sync_peers")
