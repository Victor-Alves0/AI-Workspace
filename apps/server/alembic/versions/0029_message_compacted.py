"""messages.compacted — compactação não-destrutiva

A compactação passa a NÃO apagar as mensagens: elas continuam visíveis ao
usuário e são apenas marcadas como `compacted=True` (mostradas na conversa, mas
FORA do contexto enviado à IA). O que muda é só o contexto do modelo, não o que
o usuário vê. Um divisor (mensagem is_summary) marca o ponto da compactação.

Revision ID: 0029_message_compacted
Revises: 0028_memory_used_and_status
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_message_compacted"
down_revision: Union[str, None] = "0028_memory_used_and_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("compacted", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("messages", "compacted")
