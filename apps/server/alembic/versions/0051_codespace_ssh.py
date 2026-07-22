"""Codespace — origem SSH (deploy key gerada por-projeto) e projetos locais.

`source` passa a aceitar "git-ssh" (clone via SSH; a privada da deploy key fica
criptografada aqui, mesma coluna EncryptedText usada por outros segredos) e
"local" (sem remoto — git init vazio, sem colunas novas).

Revision ID: 0051_codespace_ssh
Revises: 0050_codespace_projects
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_codespace_ssh"
down_revision: Union[str, None] = "0050_codespace_projects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("codespace_projects", sa.Column("ssh_private_key", sa.Text(), nullable=True))
    op.add_column("codespace_projects", sa.Column("ssh_public_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("codespace_projects", "ssh_public_key")
    op.drop_column("codespace_projects", "ssh_private_key")
