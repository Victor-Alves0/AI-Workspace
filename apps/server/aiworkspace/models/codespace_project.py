"""Projetos do Codespace: um repositório clonado que a IA pode ler/navegar via o
grafo de código (GraphCodeMap — tools `code.graph.query`/`code.files.browse`).

O código é a fonte da verdade; o `.codegraph/graph.db` é um cache derivado
(guardado em `/data/codespace/<user_id>/<project_id>/`, fora do repo trabalhado).
Slice 1: só leitura, origem via `git clone` HTTPS (SSH vem depois).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class CodespaceProject(Base):
    __tablename__ = "codespace_projects"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(80), default="")
    # "git" (clone HTTPS) | "git-ssh" (clone via SSH, deploy key gerada por-projeto)
    # | "local" (sem remoto — git init vazio, arquivos criados do zero)
    source: Mapped[str] = mapped_column(String(16), default="git")
    repo_url: Mapped[str] = mapped_column(Text, default="")
    branch: Mapped[str] = mapped_column(String(120), default="main")
    # conta GitHub conectada (Integrações) usada p/ autenticar o clone de repos
    # privados; None = clone anônimo (só funciona com repos públicos)
    github_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("github_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # deploy key gerada NO SERVIDOR (source="git-ssh") — a privada nunca é vista
    # pelo usuário, só a pública (ele cola no GitHub/GitLab/VPS como deploy key).
    ssh_private_key: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    ssh_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # banco de memória (mem0 "bank") criado automaticamente com o projeto — os
    # chats vinculados a este projeto compartilham memória através dele.
    memory_bank_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_banks.id", ondelete="SET NULL"), nullable=True
    )
    # modelo padrão dos NOVOS chats deste projeto (mesmo formato do
    # users.default_model: "custom:<id>" ou id do modelo base); nulo = usa o
    # padrão do usuário.
    default_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # escopo do projeto: pastas/arquivos liberados/bloqueados p/ a IA (globs),
    # aplicado DEPOIS do jail de path — {"allow": [...], "deny": [...]}
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)
    # pending -> cloning -> indexing -> ready | error
    index_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {files, symbols, edges, edges_resolved, edges_dangling, refined, index_seconds}
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
