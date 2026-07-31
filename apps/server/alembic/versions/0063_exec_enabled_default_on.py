"""Codespace: `exec_enabled` ligado por padrão (e para os projetos existentes)

Vincular um chat a um projeto já é o consentimento de trabalhar nele (como Codex/
Claude Code), então a execução no sandbox passa a vir LIGADA por padrão. A segurança
segue nas outras camadas (instalar/baixar/apagar ainda pedem confirmação; a escrita é
local/reversível). A pedido do usuário, liga também os projetos JÁ existentes — dá p/
desligar por-projeto em Configurações do projeto p/ um modo só-leitura.

Revision ID: 0063_exec_enabled_default_on
Revises: 0062_skill_files
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0063_exec_enabled_default_on"
down_revision: Union[str, None] = "0062_skill_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # novos projetos: default do banco = true
    op.alter_column("codespace_projects", "exec_enabled", server_default=sa.text("true"))
    # projetos existentes: liga a execução (pedido explícito — "para todos os projetos")
    op.execute("UPDATE codespace_projects SET exec_enabled = true WHERE exec_enabled = false")


def downgrade() -> None:
    op.alter_column("codespace_projects", "exec_enabled", server_default=sa.text("false"))
