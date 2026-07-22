"""Codespace — banco de memória por projeto.

Cada projeto ganha um `memory_bank_id` (criado automaticamente junto com o
projeto): as memórias que a IA junta enquanto trabalha nesse Codespace ficam
compartilhadas entre TODOS os chats vinculados a ele, sem depender do escopo
global. Reusa o mecanismo de "banco" já existente (ver [[memory-controller]]).

Revision ID: 0052_codespace_memory_bank
Revises: 0051_codespace_ssh
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_codespace_memory_bank"
down_revision: Union[str, None] = "0051_codespace_ssh"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("codespace_projects", sa.Column("memory_bank_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_codespace_projects_memory_bank_id", "codespace_projects", "memory_banks",
        ["memory_bank_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_codespace_projects_memory_bank_id", "codespace_projects", type_="foreignkey")
    op.drop_column("codespace_projects", "memory_bank_id")
