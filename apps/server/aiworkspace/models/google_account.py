"""Contas Google conectadas por usuário (OAuth) — Gmail + Agenda.

Cada usuário pode conectar VÁRIAS contas Google. Guardamos só o refresh token
(cifrado em repouso via EncryptedText); o access token é buscado/renovado ao vivo.
A conta de menor `position` é a principal; `client_id` diz qual app OAuth emitiu o
token (só ele consegue renová-lo).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class GoogleAccount(Base):
    __tablename__ = "google_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), default="")
    # refresh token OAuth (cifrado). O access token NÃO é persistido.
    refresh_token: Mapped[str] = mapped_column(EncryptedText, default="")
    scopes: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    client_id: Mapped[str] = mapped_column(String(200), default="", server_default="")
    broken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
