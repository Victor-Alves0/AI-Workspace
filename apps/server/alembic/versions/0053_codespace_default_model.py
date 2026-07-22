"""Codespace — modelo padrão por projeto.

Novo chat aberto pelo projeto usa este modelo (formato igual ao
`users.default_model`: "custom:<id>" ou o id do modelo base); nulo = cai no
modelo padrão do usuário. Definido pelo botão "Definir como padrão do projeto"
no cabeçalho do chat.

Revision ID: 0053_codespace_default_model
Revises: 0052_codespace_memory_bank
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053_codespace_default_model"
down_revision: Union[str, None] = "0052_codespace_memory_bank"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("codespace_projects", sa.Column("default_model", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("codespace_projects", "default_model")
