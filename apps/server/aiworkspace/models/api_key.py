"""Chaves de API do usuário + log de requisições da API pública.

A chave em claro (`aw-<prefixo>-<segredo>`) é mostrada UMA vez, na criação: o banco
guarda só o `prefix` (busca) e o `key_hash` (SHA-256 do segredo). Vazamento do banco
não vaza credencial utilizável, e a verificação continua O(1) — o prefixo é único e
indexado, então não é preciso varrer todas as chaves comparando hash.

`ApiRequest` é o log por requisição (status, latência, tokens, custo, erro). É
separado do `usage_events` de propósito: o ledger só registra o que CONSUMIU modelo,
enquanto aqui também entram 401/429/402 e requisições sem custo — que é justamente o
que se olha quando uma integração para de funcionar.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="")
    # parte pública da credencial: identifica a linha sem revelar o segredo
    prefix: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), default="")

    # permissões de endpoint: ["chat", "models:read", "memory:write", ...]
    scopes: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # {"mode": "all"|"allow", "ids": [model_config_id|base_model], "default": id,
    #  "lock": bool}  — lock=True recusa modelo não listado explicitamente (inclusive
    #  versões futuras que apareçam no catálogo depois)
    model_policy: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # {"rpm","rpd","monthly_requests","tokens_in","tokens_out","budget_usd","concurrency"}
    # 0/ausente = sem limite naquele eixo
    limits: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # {"mode": none|request|persistent|shared|key|end_user, "ttl_days", "max_items"}
    memory: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # CIDRs/IPs autorizados; vazio = qualquer origem
    ip_allowlist: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # {"url", "secret", "events": [...]}
    webhook: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # alertas de consumo já disparados neste mês: [50, 80] (evita spam de webhook)
    alerts_sent: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    enabled: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str] = mapped_column(String(64), default="", server_default="")


class ApiRequest(Base):
    __tablename__ = "api_requests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # sem FK: o histórico sobrevive à exclusão da chave (é o que se audita depois)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    key_name: Mapped[str] = mapped_column(String(120), default="", server_default="")

    endpoint: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(255), default="", server_default="")
    status: Mapped[int] = mapped_column(default=200)
    error: Mapped[str] = mapped_column(String(255), default="", server_default="")

    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
