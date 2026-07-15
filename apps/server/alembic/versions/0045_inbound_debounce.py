"""Agregação de mensagens de entrada (debounce) nos canais.

Uma janela de silêncio, por conexão, para colar as mensagens fragmentadas do
contato num único turno ("oi" + "tudo bem?" + "queria saber X" = 1 chamada ao
modelo, não 3). 0 = desligado (comportamento antigo: um turno por mensagem).

Revision ID: 0045_inbound_debounce
Revises: 0044_discord_and_github
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045_inbound_debounce"
down_revision: Union[str, None] = "0044_discord_and_github"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("whatsapp_connections", "telegram_connections", "discord_connections")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(
            t,
            sa.Column(
                "debounce_seconds",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, "debounce_seconds")
