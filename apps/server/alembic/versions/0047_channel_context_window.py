"""Janela de contexto por conexao de canal.

Quantas mensagens anteriores da conversa a IA enxerga a cada resposta, por
conexao (WhatsApp/Telegram/Discord). Padrao 40 (o valor fixo anterior);
0 = "Tudo" (com teto de seguranca aplicado no service).

Revision ID: 0047_channel_context_window
Revises: 0046_security_audit_2fa
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_channel_context_window"
down_revision: Union[str, None] = "0046_security_audit_2fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("whatsapp_connections", "telegram_connections", "discord_connections")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(
            t,
            sa.Column(
                "context_window",
                sa.Integer(),
                nullable=False,
                server_default="40",
            ),
        )


def downgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, "context_window")
