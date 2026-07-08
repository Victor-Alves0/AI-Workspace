"""users.token_version + model_configs.filter_config

- token_version: revogação de JWT (bump invalida tokens antigos).
- filter_config: parâmetros dos filtros do modelo (ex.: vision_router → modelo alvo).

Revision ID: 0018_token_version_filter_config
Revises: 0017_skills
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018_token_version_filter_config"
down_revision: Union[str, None] = "0017_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "model_configs",
        sa.Column("filter_config", JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "filter_config")
    op.drop_column("users", "token_version")
