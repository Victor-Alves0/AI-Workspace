"""Contas Slack conectadas por usuário (TOOL) — via bot/user token OU OAuth.

Espelha o [[notion_account.py]]. É a conexão da FERRAMENTA slack.workspace.manage
(agir num workspace sob demanda) — distinta do CANAL Slack (mirror de conversas), que
usa outras tabelas. O token do Slack não expira (salvo rotação, que não habilitamos),
então guardamos só o token cifrado + o nome do time. As credenciais do OAuth (client
id/secret) são globais e ficam em app_settings.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class SlackAccount(Base):
    __tablename__ = "slack_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    team: Mapped[str] = mapped_column(String(160), default="")
    team_id: Mapped[str] = mapped_column(String(32), default="")
    bot_user_id: Mapped[str] = mapped_column(String(32), default="")
    # bot token (xoxb-…) ou user token (xoxp-…), colado OU vindo do OAuth (cifrado)
    token: Mapped[str] = mapped_column(EncryptedText, default="")
    # "token" (colado) | "oauth"
    auth_type: Mapped[str] = mapped_column(String(8), default="token")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
