"""Máquinas remotas (VPS, servidor de casa, workstation) onde a IA tem terminal.

O agente (`agent/aiw_remote_agent.py`) é instalado NA MÁQUINA e expõe uma API HTTPS
autenticada por token; o AI Workspace é quem DISCA. Nada de SSH: a superfície é uma
API mínima (exec/jobs/egress), o token é por-host e revogável, e o agente aplica a
política de saída localmente — coisas que uma sessão SSH crua não dá.

Duas camadas de rede, independentes e ambas com killswitch:

  1. SAÍDA DO WORKSPACE → agente (`proxy_url`, `require_proxy`). "Sempre falar com X
     usando Y". Com `require_proxy` ligado, proxy ausente/quebrado = a conexão NÃO
     acontece; nunca há fallback direto (o fallback É o vazamento de IP).

  2. SAÍDA DOS COMANDOS na máquina remota (`egress`/`egress_proxy`). O agente força
     todo tráfego dos comandos pelo proxy Y, com DNS pelo proxy e regra de firewall
     por-uid que DESCARTA o resto — se o proxy cai, o comando falha em vez de sair
     pelo IP real. Ver [[remote-terminal]].

Segredos (token do agente e URLs de proxy, que costumam levar usuário:senha) usam a
mesma coluna `EncryptedText` dos demais — cifrados em repouso.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class RemoteHost(Base):
    __tablename__ = "remote_hosts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    # identificador estável e legível que o MODELO usa p/ escolher a máquina
    # ("vps-oracle") — o uuid não é digitável numa chamada de ferramenta.
    slug: Mapped[str] = mapped_column(String(80), default="", index=True)
    # endereço do agente, ex.: "https://203.0.113.10:8791" (ou o host de um túnel)
    base_url: Mapped[str] = mapped_column(Text, default="")
    # segredo compartilhado com o agente (Authorization: Bearer)
    token: Mapped[str] = mapped_column(EncryptedText, default="")

    # --- TLS ---------------------------------------------------------------
    # "pinned" = valida contra o certificado do próprio agente colado abaixo
    # (autoassinado; pinagem real, sem depender de SAN/CA pública);
    # "system"  = cadeia pública normal (agente atrás de proxy reverso com cert);
    # "off"     = sem verificação — só faz sentido DENTRO de um túnel confiável.
    tls_mode: Mapped[str] = mapped_column(String(8), default="pinned")
    tls_cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- camada 1: saída do workspace até o agente -------------------------
    # "socks5://user:pass@127.0.0.1:9050" | "http://..." (vazio = direto)
    proxy_url: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    # killswitch DESTE lado: sem proxy utilizável, recusa conectar (nunca cai
    # para a rota direta — seria justamente o vazamento que o proxy evita).
    require_proxy: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # --- camada 2: saída dos comandos na máquina remota ---------------------
    # knobs não-secretos: {"mode": "off|env|force", "dns": "proxy|system",
    #  "killswitch": bool, "allow_lan": bool, "allow_hosts": ["1.2.3.4"]}
    egress: Mapped[dict] = mapped_column(JSONB, default=dict)
    # o proxy que o AGENTE deve usar (viaja até ele quando a política é aplicada)
    egress_proxy: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)

    # --- execução -----------------------------------------------------------
    workdir: Mapped[str] = mapped_column(Text, default="")
    shell: Mapped[str] = mapped_column(String(120), default="")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120, server_default="120")
    # Terminal de uma máquina de verdade (muitas vezes root) é mais sensível que o
    # sandbox do Codespace: aqui a confirmação é POR-HOST e vem LIGADA, sem depender
    # do toggle global de "confirmar ações".
    confirm_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # --- estado observado (preenchido pelo ping) ----------------------------
    # unknown | online | offline | unauthorized | blocked (killswitch impediu)
    status: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_version: Mapped[str] = mapped_column(String(32), default="")
    # {hostname, os, kernel, user, root, shell, egress:{...}, public_ip}
    info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
