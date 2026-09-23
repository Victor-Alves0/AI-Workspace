"""Memória nativa: a tabela que o mem0 criava sozinho passa a vir da migração.

O mem0 criava `aiworkspace_memories` na primeira vez que rodava. Sem ele, a tabela
precisa existir desde o boot (instalação nova). Mesmo layout do mem0, então as
instalações que já têm a tabela (com as memórias dos usuários) não mudam nada.
O índice por usuário atende toda consulta (sempre filtrada por payload->>'user_id').

Revision ID: 0079_native_memories
Revises: 0078_chat_mini_app
Create Date: 2026-09-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0079_native_memories"
down_revision: str | None = "0078_chat_mini_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "CREATE TABLE IF NOT EXISTS aiworkspace_memories ("
        " id UUID PRIMARY KEY, vector vector(384), payload JSONB)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_aiworkspace_memories_user "
        "ON aiworkspace_memories ((payload->>'user_id'))"
    )


def downgrade() -> None:
    # a tabela guarda as memórias dos usuários (e já existia antes desta migração,
    # criada pelo mem0): voltar a versão não a apaga
    op.execute("DROP INDEX IF EXISTS ix_aiworkspace_memories_user")
