"""Cache dos efeitos sonoros gerados (porta batendo, espada, rosnado...).

A IA escreve um marcador `[[som: ...]]` na narração e a interface o mostra como um
botão que toca o som. Gerar custa créditos da ElevenLabs e leva alguns segundos —
então cada descrição é gerada UMA vez por usuário e reaproveitada para sempre: a
segunda "porta batendo" da campanha toca na hora e não custa nada.

O áudio fica em `generated_images` (a tabela de mídia gerada, que já serve por URL
assinada com Range); aqui só se guarda a chave da descrição → mídia.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SoundEffect(Base):
    __tablename__ = "sound_effects"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # sha256 da descrição normalizada (minúsculas, espaços colapsados)
    key: Mapped[str] = mapped_column(String(64), index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    media_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_images.id", ondelete="CASCADE")
    )

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_sound_effect_user_key"),)
