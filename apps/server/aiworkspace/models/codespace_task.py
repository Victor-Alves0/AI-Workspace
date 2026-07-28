"""Tarefa de código do Codespace: um WORKTREE git isolado (branch própria) onde um
agente trabalha sem colidir com o `src` do projeto nem com outros agentes.

Ciclo: `running` (worktree aberto, agente escrevendo) → `awaiting_review` (terminou;
diff pronto p/ revisão) → `merged` (mesclado no branch do projeto) | `discarded`
(worktree/branch removidos) | `error` (falha/conflito). O worktree físico vive em
`<codespace_data>/<user>/<project>/wt/<task_id>/` e é removido no merge/discard.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class CodespaceTask(Base):
    __tablename__ = "codespace_tasks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("codespace_projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # chat que originou a tarefa (opcional — pode ser criada headless/por automação)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    # quem trabalhou: rótulo do subagente ("Backend") ou "" para o agente principal
    agent: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    # branch do worktree (ex.: "codespace/<task_id>") e branch base de onde saiu
    branch: Mapped[str] = mapped_column(String(255), default="")
    base_branch: Mapped[str] = mapped_column(String(255), default="")
    # caminho ABSOLUTO do worktree no servidor (dentro do codespace_data)
    worktree_path: Mapped[str] = mapped_column(Text, default="")
    # running | awaiting_review | merged | discarded | error
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    # {files, insertions, deletions} — resumo do diff vs. base
    diff_stat: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # None | "pass" | "fail" — resultado do test_command na última verificação
    test_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
