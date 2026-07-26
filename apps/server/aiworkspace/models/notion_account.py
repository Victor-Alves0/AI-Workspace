"""Contas Notion conectadas por usuário — via token de integração interna OU OAuth.

Espelha o [[github_account.py]]. Diferença: o token do Notion NÃO expira e não tem
refresh (mesmo no OAuth o Notion emite um bot token permanente), então guardamos só o
token cifrado + o nome do workspace. As credenciais do OAuth (client id/secret) são
globais e ficam em app_settings.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class NotionAccount(Base):
    __tablename__ = "notion_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace: Mapped[str] = mapped_column(String(160), default="")
    bot_id: Mapped[str] = mapped_column(String(64), default="")
    # token de integração interna OU bot token OAuth (cifrado)
    token: Mapped[str] = mapped_column(EncryptedText, default="")
    # "token" (integração interna colada) | "oauth"
    auth_type: Mapped[str] = mapped_column(String(8), default="token")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
