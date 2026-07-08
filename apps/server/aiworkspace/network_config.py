"""Config de rede gerida pelo admin.

- allowlist de IP: aplicada em RUNTIME (middleware) — cache em memória p/ não
  bater no banco a cada request; recarregado no startup e a cada alteração.
- host/porta/repo: persistidos p/ o painel e p/ o script de deploy (update.sh);
  porta/host só têm efeito no próximo `docker compose up` (o container não
  reabre a própria porta sozinho — por isso a abordagem "script no host").
"""

from __future__ import annotations

import ipaddress
import logging

from sqlalchemy import select

from .db import SessionLocal
from .models import AppSetting

logger = logging.getLogger(__name__)

NETWORK_KEY = "network"

_allowlist: list[str] = []
_networks: list = []


def _parse(nets: list[str]) -> list:
    out = []
    for n in nets or []:
        s = (n or "").strip()
        if not s:
            continue
        try:
            out.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            logger.warning("IP/CIDR inválido no allowlist ignorado: %s", s)
    return out


def set_allowlist(ips: list[str]) -> None:
    global _allowlist, _networks
    _allowlist = [i.strip() for i in (ips or []) if i and i.strip()]
    _networks = _parse(_allowlist)


def get_allowlist() -> list[str]:
    return list(_allowlist)


def is_allowed(client_ip: str | None) -> bool:
    """True se o IP passa no allowlist. Allowlist VAZIA = libera todos (padrão)."""
    if not _networks:
        return True
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(ip in net for net in _networks)


async def load_config() -> dict:
    """Lê a config de rede do banco e atualiza o cache do allowlist."""
    async with SessionLocal() as db:
        row = await db.scalar(select(AppSetting).where(AppSetting.key == NETWORK_KEY))
        cfg = (row.value.get("v") if row and isinstance(row.value, dict) else None) or {}
    set_allowlist(cfg.get("allowed_ips") or [])
    return cfg


async def save_config(cfg: dict) -> dict:
    async with SessionLocal() as db:
        row = await db.scalar(select(AppSetting).where(AppSetting.key == NETWORK_KEY))
        if row is None:
            db.add(AppSetting(key=NETWORK_KEY, value={"v": cfg}))
        else:
            row.value = {"v": cfg}
        await db.commit()
    set_allowlist(cfg.get("allowed_ips") or [])
    return cfg
