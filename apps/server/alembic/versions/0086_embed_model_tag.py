"""Cada vetor da Base de Conhecimento guarda o modelo que o gerou.

A busca semântica (Base de Conhecimento + memória) trocou o `bge-small-en` (só
inglês) por um modelo multilíngue do mesmo tamanho (384). Vetores de modelos
diferentes não se comparam: com a etiqueta, o recálculo em segundo plano
(`knowledge/reembed.py`) acha e refaz os antigos — inclusive os que chegarem pela
sincronização vindos de uma instância ainda não atualizada. Vazio = modelo antigo.

Revision ID: 0086_embed_model_tag
Revises: 0085_chat_workspace_folder
Create Date: 2026-09-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086_embed_model_tag"
down_revision: str | None = "0085_chat_workspace_folder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_chunks",
                  sa.Column("embed_model", sa.String(120), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embed_model")
