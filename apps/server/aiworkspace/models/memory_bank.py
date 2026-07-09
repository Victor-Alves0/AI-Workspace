"""Banco de memória: coleção nomeada de memórias compartilhável entre modelos.

Vários ModelConfigs podem "acoplar" o mesmo banco (em capabilities.memory.banks)
e passam a ler/escrever nele — compartilhando memórias entre si SEM depender do
escopo global. As memórias em si ficam no mem0 com agent_id "bank:<id>"; esta
tabela é só o registro (permite criar/nomear/excluir bancos, inclusive vazios).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MemoryBank(Base):
    __tablename__ = "memory_banks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
