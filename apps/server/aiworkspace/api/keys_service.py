"""Geração, verificação e políticas das chaves de API.

Formato da chave: ``aw-<prefixo>-<segredo>``. O prefixo (12 chars) é público e
único: identifica a linha em uma única query indexada. O segredo (43 chars, 32
bytes de entropia) só existe na resposta da criação; o banco guarda o SHA-256.

SHA-256 puro (sem bcrypt/argon2) é a escolha certa AQUI e errada para senhas: o
segredo é aleatório de 256 bits, então não há dicionário a atacar — e o hash roda
a cada requisição da API, onde um KDF lento viraria o gargalo.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
from datetime import datetime, timezone
from typing import Any

KEY_PREFIX = "aw"
# hex, NÃO base64url: o delimitador da chave é "-", e o alfabeto base64url inclui
# "-"/"_" — um token com "-" faria o parse dividir no lugar errado e rejeitar uma
# chave legítima no login. Hex (0-9a-f) nunca colide com o separador.
_PREFIX_BYTES = 6   # -> 12 chars em hex
_SECRET_BYTES = 32  # -> 64 chars em hex (256 bits de entropia)

# permissões por endpoint. "chat" é a única indispensável para usar modelos.
ALL_SCOPES = (
    "chat",
    "models:read",
    "memory:read",
    "memory:write",
    "files:read",
    "files:write",
    "usage:read",
)
DEFAULT_SCOPES = ("chat", "models:read", "usage:read")

MEMORY_MODES = ("none", "request", "persistent", "shared", "key", "end_user")

_KEY_RE = re.compile(r"^aw-([0-9a-f]{8,32})-([0-9a-f]{32,128})$")


def generate() -> tuple[str, str, str]:
    """Cria uma credencial nova: (chave_em_claro, prefixo, hash)."""
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    return f"{KEY_PREFIX}-{prefix}-{secret}", prefix, hash_secret(secret)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse(raw: str) -> tuple[str, str] | None:
    """(prefixo, segredo) de uma chave em claro; None se o formato não bate."""
    m = _KEY_RE.match((raw or "").strip())
    return (m.group(1), m.group(2)) if m else None


def verify(secret: str, key_hash: str) -> bool:
    return secrets.compare_digest(hash_secret(secret), key_hash or "")


def masked(prefix: str) -> str:
    """Representação exibível no painel — nunca reconstrói o segredo. ASCII de
    propósito: este texto viaja por terminais e logs que nem sempre são UTF-8."""
    return f"{KEY_PREFIX}-{prefix}-{'*' * 8}"


# --------------------------------------------------------------------------- #
# Estado da chave
# --------------------------------------------------------------------------- #

def key_state(key: Any, now: datetime | None = None) -> str:
    """'active' | 'disabled' | 'revoked' | 'expired'. Um único lugar decide isso
    para que painel e autenticação nunca discordem sobre o que a chave é."""
    now = now or datetime.now(timezone.utc)
    if key.revoked_at:
        return "revoked"
    if not key.enabled:
        return "disabled"
    exp = key.expires_at
    if exp is not None:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            return "expired"
    return "active"


def has_scope(key: Any, scope: str) -> bool:
    scopes = list(key.scopes or [])
    return scope in scopes


# --------------------------------------------------------------------------- #
# Política de IP
# --------------------------------------------------------------------------- #

def ip_allowed(key: Any, ip: str | None) -> bool:
    """Allowlist vazia libera geral. Aceita IP solto ou CIDR; entrada inválida na
    lista é ignorada (nunca deixa a lista inteira falhar aberta ou fechada)."""
    rules = [str(r).strip() for r in (key.ip_allowlist or []) if str(r).strip()]
    if not rules:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for rule in rules:
        try:
            if "/" in rule:
                if addr in ipaddress.ip_network(rule, strict=False):
                    return True
            elif addr == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False


# --------------------------------------------------------------------------- #
# Política de modelos
# --------------------------------------------------------------------------- #

def model_allowed(key: Any, model_id: str) -> bool:
    """`mode="all"` acompanha o catálogo; `mode="allow"` libera só a lista.

    A lista é a resposta a "impedir acesso a futuras versões": um modelo que
    aparecer no catálogo DEPOIS da criação da chave não entra sozinho — só se o
    dono editar a chave. É por isso que a política guarda ids explícitos em vez de
    um padrão de nome.
    """
    pol = key.model_policy or {}
    if (pol.get("mode") or "all") != "allow":
        return True
    ids = {str(i) for i in (pol.get("ids") or [])}
    return str(model_id) in ids


def default_model(key: Any) -> str:
    return str((key.model_policy or {}).get("default") or "")


# --------------------------------------------------------------------------- #
# Política de memória
# --------------------------------------------------------------------------- #

def memory_mode(key: Any) -> str:
    mode = (key.memory or {}).get("mode")
    return mode if mode in MEMORY_MODES else "none"


def memory_scope_id(key: Any, user_id: str, end_user: str | None) -> str | None:
    """A quem a memória pertence nesta requisição.

    - none/request  -> None (nada é lido nem gravado no mem0)
    - persistent    -> o próprio usuário: a API compartilha a memória do app
    - shared        -> idem, explicitando que várias chaves veem a mesma coisa
    - key           -> isolada por chave (`apikey:<id>`)
    - end_user      -> isolada por usuário final (`enduser:<key>:<id>`), o modo
                       certo para quem revende a API a terceiros
    """
    mode = memory_mode(key)
    if mode in ("none", "request"):
        return None
    if mode in ("persistent", "shared"):
        return str(user_id)
    if mode == "key":
        return f"apikey:{key.id}"
    if not end_user:
        return None
    return f"enduser:{key.id}:{end_user}"
