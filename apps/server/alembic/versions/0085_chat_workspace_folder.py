"""Pasta de trabalho por chat (seletor de pastas) + fim do "espaço do chat" oculto.

Antes, um chat sem projeto ganhava um projeto ESCONDIDO ("Espaço do chat: …") no
primeiro pedido de arquivo — a IA gravava lá e o usuário não achava os arquivos.
Agora cada chat aponta para uma pasta visível (`chats.workspace`). Os espaços que já
existiam viram projetos normais (aparecem no Codespace) e ficam ligados ao seu chat:
nada é apagado, e apagar o chat não leva mais os arquivos junto.

Revision ID: 0085_chat_workspace_folder
Revises: 0084_obs_analysis_indexes
Create Date: 2026-09-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0085_chat_workspace_folder"
down_revision: str | None = "0084_obs_analysis_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("workspace", sa.String(64), nullable=True))
    op.execute("""
        UPDATE chats c SET workspace = p.id::text
        FROM codespace_projects p
        WHERE p.scope ? 'chat_workspace' AND p.scope->>'chat_workspace' = c.id::text
          AND p.user_id = c.user_id
    """)
    op.execute("""
        UPDATE codespace_projects
        SET scope = (scope - 'chat_workspace') || jsonb_build_object('from_chat', scope->>'chat_workspace'),
            name = CASE WHEN name LIKE 'Espaço do chat: %' THEN substr(name, 17) ELSE name END
        WHERE scope ? 'chat_workspace'
    """)


def downgrade() -> None:
    op.drop_column("chats", "workspace")
