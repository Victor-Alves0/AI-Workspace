"""Imagens geradas pela IA (GenImage Router).

Os bytes ficam no banco (LargeBinary) e são servidos sob demanda por
`GET /images/{id}?t=<sig>` — a mensagem guarda só uma URL assinada pequena, então o
base64 nunca entra no contexto do modelo nem incha o `tool_events`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class GeneratedImage(Base):
    __tablename__ = "generated_images"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    mime: Mapped[str] = mapped_column(String(64), default="image/png")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(255), default="")
