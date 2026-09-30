"""Contas Google: ordem (a 1ª é a principal), app que emitiu o token e estado.

- `position`: a conta de menor posição é a PRINCIPAL — a que a IA usa quando o
  pedido não diz qual; as seguintes são o fallback (se o usuário ligar).
- `client_id`: o app OAuth que emitiu o refresh token. O token só renova com o
  mesmo app; sem isso, trocar o app (próprio ↔ embutido) quebrava as contas antigas
  em silêncio. Vazio = conta antiga → usa o app em vigor.
- `broken_at`/`last_error`: o Google recusou renovar (acesso revogado/expirado). A
  tela mostra "Reconectar" em vez de a IA descobrir no meio de um pedido.

Revision ID: 0087_google_account_order
Revises: 0086_embed_model_tag
Create Date: 2026-09-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_google_account_order"
down_revision: str | None = "0086_embed_model_tag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("google_accounts",
                  sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("google_accounts",
                  sa.Column("client_id", sa.String(200), nullable=False, server_default=""))
    op.add_column("google_accounts",
                  sa.Column("broken_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("google_accounts",
                  sa.Column("last_error", sa.Text(), nullable=False, server_default=""))
    # contas que já existem ganham a ordem em que foram conectadas
    op.execute(
        "UPDATE google_accounts g SET position = r.n FROM ("
        " SELECT id, row_number() OVER (PARTITION BY user_id ORDER BY created_at) - 1 AS n"
        " FROM google_accounts) r WHERE g.id = r.id"
    )


def downgrade() -> None:
    op.drop_column("google_accounts", "last_error")
    op.drop_column("google_accounts", "broken_at")
    op.drop_column("google_accounts", "client_id")
    op.drop_column("google_accounts", "position")
