"""model_configs.slug — identificador do modelo editável pelo usuário

O editor de modelos passa a permitir escrever/customizar o "ID do Modelo".
Guardado aqui (opcional, sem unicidade forçada). Ausente = derivado do nome.

Revision ID: 0030_model_config_slug
Revises: 0029_message_compacted
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_model_config_slug"
down_revision: Union[str, None] = "0029_message_compacted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("model_configs", sa.Column("slug", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("model_configs", "slug")
