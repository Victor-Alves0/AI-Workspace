"""Durabilidade das gerações de mídia que terminam DEPOIS do turno (ver
integrations/civitai_service.py).

A fila em memória (`_pending_watch`) não sobrevive a um restart — e a mídia já foi
PAGA (Buzz). Sem sombra no banco, reiniciar o servidor no meio de uma geração some
com a imagem em silêncio: é a mesma classe do "wake que nunca chega" dos jobs de
exec, com dinheiro envolvido.

Esta tabela guarda só o necessário para retomar a espera no boot: qual workflow, de
quem e para qual chat. O token NÃO entra aqui — ele é lido dos segredos do usuário na
recuperação, então uma troca de chave não deixa credencial velha espalhada.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MediaJob(Base):
    __tablename__ = "media_jobs"

    # provedor da geração (civitai hoje; higgsfield/outros podem reusar a tabela)
    provider: Mapped[str] = mapped_column(String(32), default="civitai", index=True)
    # id do workflow no provedor — é por ele que a retomada consulta o status
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=True, index=True
    )
    prompt: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    # SETTLED = a mídia (ou o erro) já foi entregue no chat. `settled=False` = a
    # entrega ficou devendo, e a recuperação do boot volta a esperar por ela.
    settled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )
