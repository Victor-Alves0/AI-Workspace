"""Skills: documentos de habilidade carregados sob demanda (lazy loading).

Uma Skill tem nome + descrição (baratos, sempre visíveis ao modelo) e um
conteúdo completo (caro). O modelo recebe inicialmente só nome+descrição; quando
percebe que precisa, chama a meta-ferramenta `view_skill` e o conteúdo completo é
injetado. No chat, o usuário também pode invocá-las com `$` no campo de mensagem.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_skill_slug"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # identificador estável, ex.: "revisao_de_codigo" (invocado como "$revisao_de_codigo")
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    # descrição curta: SEMPRE visível ao modelo (o "quando usar")
    description: Mapped[str] = mapped_column(Text, default="")
    # conteúdo completo: carregado sob demanda via view_skill (o "como fazer")
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
