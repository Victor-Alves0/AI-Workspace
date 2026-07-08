"""Ferramentas criadas pelo usuário (código executado e registrado na SIFT)."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Tool(Base):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("user_id", "path", name="uq_tool_path"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # caminho hierárquico na SIFT, ex.: "custom.clima.previsao"
    path: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    # params no formato SIFT: {"cidade": "string:o::nome da cidade"}
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    # campos de resposta a manter (filtragem da SIFT)
    returns: Mapped[list] = mapped_column(JSONB, default=list)
    # código Python; deve definir `def run(**params): ...`
    code: Mapped[str] = mapped_column(Text, default="")
    # valores das "valves" (configurações ajustáveis definidas no código via VALVES)
    valves: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # organização na aba Ferramentas (filtro/agrupamento)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    # "code" = código Python local; "mcp" = integração com servidor MCP externo
    tool_type: Mapped[str] = mapped_column(String(20), default="code")
    # config da integração MCP: {"url": ..., "transport": "http", "headers": {...}}
    mcp_config: Mapped[dict] = mapped_column(JSONB, default=dict)
