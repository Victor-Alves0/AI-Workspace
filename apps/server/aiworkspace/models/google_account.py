"""Contas Google conectadas por usuário (OAuth) — Gmail + Agenda.

Cada usuário pode conectar VÁRIAS contas Google. Guardamos só o refresh token
(cifrado em repouso via EncryptedText); o access token é buscado/renovado ao vivo.
As credenciais do app OAuth (client id/secret) são globais e ficam em app_settings.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
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
