"""Modelos personalizados (estilo "Models" do OpenWebUI)."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ModelConfig(Base):
    __tablename__ = "model_configs"
    # O slug é o ID público do preset na API. ``NULL`` continua permitido para
    # modelos que usam somente o UUID interno, mas dois IDs explícitos iguais do
    # mesmo usuário fariam a API resolver "o primeiro" modelo encontrado.
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_model_config_user_slug"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # modelo-base no OpenRouter, ex.: "deepseek/deepseek-v4-flash"
    base_model: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    # identificador editável pelo usuário (vanity id); ausente = derivado do nome
    slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    # capacidades ligadas (vision, file_upload, image_generation, chat_context) —
    # o que o modelo PODE fazer. chat_context=false remove o histórico do turno.
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    # config dos filtros (chaves "filter:<key>" ficam em `capabilities`; os
    # parâmetros de cada filtro ficam aqui). Ex.: vision_router → modelo de visão
    # que descreve as imagens p/ um modelo sem visão:
    #   {"vision_router": {"model": "<base_model_id>"}}
    filter_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # chave-mestra "SIFT": quando False o modelo não usa NENHUMA ferramenta
    tools_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # ferramentas habilitadas p/ este modelo: uuid de Tool do usuário ou "builtin:<path>"
    tool_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # code mode da SIFT: o modelo orquestra ferramentas escrevendo Python
    # (run_code em sandbox de subprocesso). Só vale com tools_enabled=True.
    code_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    # como o SIFT é apresentado ao modelo (o "quando usar"):
    #   {"mode": "prompt"|"list", "prompt": <texto quando-usar>, "pinned": [tool_id...]}
    #   - "prompt" (padrão): injeta um prompt curto de "quando usar" (barato)
    #   - "list": injeta a lista de ferramentas (mais caro; volta ao catálogo)
    #   - pinned: ferramentas fixadas (specs de 1a classe, sem discovery)
    sift_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # skills equipadas neste modelo (uuid de Skill): o modelo vê nome+descrição e
    # carrega o conteúdo completo sob demanda via view_skill (lazy loading)
    skill_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # sugestões de prompt iniciais
    prompt_suggestions: Mapped[list] = mapped_column(JSONB, default=list)
    tts_voice: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
