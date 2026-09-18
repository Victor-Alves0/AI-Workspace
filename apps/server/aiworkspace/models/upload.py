"""Arquivos enviados pelo usuário no chat, guardados FORA da mensagem.

Antes o anexo viajava e era persistido embutido (base64 dentro da própria mensagem).
Isso põe um teto baixo e rígido: o JSON inteiro é carregado na memória do servidor a
cada envio, e a linha da mensagem cresce com o arquivo. Um PDF grande derrubava o
processo em vez de ser recusado.

Agora o arquivo sobe por conta própria (multipart, direto para o disco) e a mensagem
guarda só a REFERÊNCIA. O binário fica em `uploads_dir/<user>/<id>`, servido por URL
assinada — o mesmo desenho da mídia gerada. `text` guarda a extração dos documentos,
para o turno não reprocessar o arquivo a cada regeneração.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Upload(Base):
    __tablename__ = "uploads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # o chat só é conhecido quando a mensagem é enviada (o envio pode nem acontecer)
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    # image | audio | file — decide como o turno usa o arquivo
    kind: Mapped[str] = mapped_column(String(16), default="file", index=True)
    # caminho relativo dentro de `uploads_dir` (nunca absoluto: o volume pode mudar)
    path: Mapped[str] = mapped_column(String(512), default="")
    # texto extraído de documentos (PDF/DOCX/XLSX/PPTX/CSV), quando houver
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ANEXADO = já foi usado numa mensagem. O que nunca foi anexado é lixo de um
    # envio abandonado e o reaper apaga depois de algumas horas.
    attached: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
