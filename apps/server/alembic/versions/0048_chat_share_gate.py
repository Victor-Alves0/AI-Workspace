"""Senha e validade opcionais do link público de um chat compartilhado.

- `chats.public_password_hash`: hash argon2 da senha do link (NULL = sem senha).
- `chats.public_expires_at`: validade do link (NULL = sem prazo). NÃO apaga o chat
  (diferente de `expires_at`, que é o TTL de auto-exclusão das automações) — só
  invalida o link público.

Revision ID: 0048_chat_share_gate
Revises: 0047_channel_context_window
Create Date: 2026-07-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_chat_share_gate"
down_revision: Union[str, None] = "0047_channel_context_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("public_password_hash", sa.String(length=255), nullable=True))
    op.add_column("chats", sa.Column("public_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "public_expires_at")
    op.drop_column("chats", "public_password_hash")
