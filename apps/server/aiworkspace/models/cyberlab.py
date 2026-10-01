"""Estado autoritativo do CyberLab.

Um caso de avaliação de segurança guiada: a IA conduz um OPERADOR HUMANO (ela não
executa nada — instrui o passo, o humano roda e cola o resultado, a IA interpreta e
segue o loop). Começa pelo modo `blackbox`; outros modos (reverse engineering, blue
team…) entram pelo mesmo schema, só mudando `mode` e o protocolo do turno.

Um caso pertence a um chat (como a campanha do Imaginai): apagar o chat apaga o caso.
O estado do loop (passos e achados) fica em `settings` JSONB — a IA propõe, mas só
estas colunas definem o que o caso É.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class CyberLabCase(Base):
    __tablename__ = "cyberlab_cases"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Um chat é um caso. Apagar o chat remove o caso inteiro.
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Modo de caso: blackbox | revory (reverse engineering) | blueteam | ...
    # Novos modos entram só trocando o protocolo do turno, sem mexer no schema.
    mode: Mapped[str] = mapped_column(String(32), default="blackbox")
    # Escopo do caso. `authorization` registra que o alvo é autorizado — é o campo que
    # o relatório final precisa e a base dos guard-rails que entram no fim. Não gateia
    # nada hoje; só guarda o que o operador declarou.
    target: Mapped[str] = mapped_column(Text, default="")
    authorization: Mapped[str] = mapped_column(Text, default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    # Fase do loop: scoping → recon → enum → analysis → reporting.
    phase: Mapped[str] = mapped_column(String(32), default="scoping")
    # Pipeline do caso: {"log": [{seq, phase, command, output, note}], "findings":
    # [{seq, title, severity, detail, refs}]}. A IA escreve via a tool `cyberlab`.
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
