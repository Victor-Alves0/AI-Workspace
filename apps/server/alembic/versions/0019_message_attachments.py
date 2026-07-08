"""messages.attachments — imagens/arquivos anexados pelo usuário

Revision ID: 0019_message_attachments
Revises: 0018_token_version_filter_config
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0019_message_attachments"
down_revision: Union[str, None] = "0018_token_version_filter_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("attachments", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "attachments")
