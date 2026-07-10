"""WhatsApp: prompt por número, limites de uso, roles por contato e pasta

Novos campos em whatsapp_connections:
- system_prompt: prompt adicional do número, concatenado ao do modelo;
- limits: limites de mensagens por contato ({total, per_hour, per_day, per_month});
- contacts: contexto/roles por número ([{number, name, role, context}]);
- folder_id: pasta "Chats" da conexão (estrutura WhatsApp/<número>/Chats).

Revision ID: 0035_whatsapp_prompt_limits
Revises: 0034_memory_banks
Create Date: 2026-07-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0035_whatsapp_prompt_limits"
down_revision: Union[str, None] = "0034_memory_banks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_connections",
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "whatsapp_connections",
        sa.Column("limits", JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "whatsapp_connections",
        sa.Column("contacts", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "whatsapp_connections",
        sa.Column("folder_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_whatsapp_connections_folder_id",
        "whatsapp_connections",
        "folders",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_whatsapp_connections_folder_id", "whatsapp_connections", type_="foreignkey"
    )
    op.drop_column("whatsapp_connections", "folder_id")
    op.drop_column("whatsapp_connections", "contacts")
    op.drop_column("whatsapp_connections", "limits")
    op.drop_column("whatsapp_connections", "system_prompt")
