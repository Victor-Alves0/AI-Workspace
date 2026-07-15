"""Contas GitHub conectadas por usuário — via Personal Access Token (PAT) ou OAuth.

Espelha o GoogleAccount ([[google_account.py]]). Cada usuário pode conectar várias
contas/tokens. O token (PAT ou access token OAuth) é cifrado em repouso; para OAuth
guardamos também o refresh (quando o app emite tokens expiráveis). As credenciais do
OAuth App (client id/secret) são globais e ficam em app_settings.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class GithubAccount(Base):
    __tablename__ = "github_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    login: Mapped[str] = mapped_column(String(120), default="")
    # PAT ou access token OAuth (cifrado)
    token: Mapped[str] = mapped_column(EncryptedText, default="")
    # refresh token OAuth (cifrado; vazio p/ PAT ou OAuth sem expiração)
    refresh_token: Mapped[str] = mapped_column(EncryptedText, default="")
    # "pat" | "oauth"
    auth_type: Mapped[str] = mapped_column(String(8), default="pat")
    scopes: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    # expiração do access token OAuth (null = não expira / PAT)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
