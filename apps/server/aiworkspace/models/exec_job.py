"""Durabilidade dos comandos em BACKGROUND do Codespace (ver codespace/exec_jobs.py).

O registro em memória (`_jobs`) não sobrevive a restart — e o subprocesso também não.
O problema é o **wake que nunca chega**: um chat que soltou um job longo e encerrou o
turno esperando ser ACORDADO ao término fica travado pra sempre se o servidor reinicia.

Esta tabela espelha o ciclo de vida do job em disco só o suficiente p/ a RECUPERAÇÃO
no boot: achar os jobs que ficaram devendo um desfecho (`settled=False`), marcá-los
como interrompidos e ACORDAR o chat com um aviso — recuperação graciosa em vez de
perda silenciosa. Não é ressurreição do processo (impossível); é destravar o chat.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ExecJob(Base):
    __tablename__ = "codespace_exec_jobs"

    # a chave do Job em memória (uuid4().hex[:12]) — o id uuid é o da Base.
    job_key: Mapped[str] = mapped_column(String(16), index=True, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("codespace_projects.id", ondelete="SET NULL"), nullable=True
    )
    worktree: Mapped[str | None] = mapped_column(String(64), nullable=True)
    command: Mapped[str] = mapped_column(Text, default="")
    # running | done | failed | killed | interrupted (interrupted = cortado por restart)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # SETTLED = o desfecho já foi entregue (o chat foi acordado OU um wait consumiu
    # inline). `settled=False` num job não-rodando = ÓRFÃO que a recuperação deve tratar.
    settled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    output_tail: Mapped[str] = mapped_column(Text, default="")
