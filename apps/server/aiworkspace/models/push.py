"""Web Push: inscrições de notificação push do navegador (por dispositivo).

Cada navegador que o usuário autoriza gera uma PushSubscription (endpoint + chaves
p256dh/auth). O servidor envia notificações (ex.: automações) via Web Push usando
as chaves VAPID globais (geradas uma vez, em app_settings). Ver `push_service`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # endpoint do push service do navegador (único por inscrição)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255), default="")
    auth: Mapped[str] = mapped_column(String(255), default="")
    # rótulo do agente (navegador/SO) só p/ o usuário reconhecer o dispositivo
    ua: Mapped[str] = mapped_column(String(255), default="")
