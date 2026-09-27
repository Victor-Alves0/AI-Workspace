"""Instâncias pareadas para sincronização (servidor ↔ desktop ↔ …). Ver `sync/`.

`id` é o id da OUTRA instância (o mesmo que ela usa como origem das mudanças).
`active` = esta instância é quem inicia a troca (tem a URL e alcança a outra — o
desktop alcança o servidor; o contrário, atrás de NAT, não). `user_map` liga as contas
em comum pelo e-mail: {id do usuário lá: id do usuário aqui}.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class SyncPeer(Base):
    __tablename__ = "sync_peers"

    name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # segredo do par (chave do envelope cifrado das trocas), cifrado em repouso
    secret: Mapped[str] = mapped_column(EncryptedText)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    user_map: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # até onde as MINHAS mudanças já foram enviadas / as DELA já foram recebidas
    push_cursor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    pull_cursor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    remote_schema: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
