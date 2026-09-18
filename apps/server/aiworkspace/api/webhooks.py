"""Webhooks dos eventos da API.

Entrega assinada (HMAC-SHA256 sobre o corpo bruto) para o destino confiar na
origem, e **fire-and-forget**: a entrega roda numa task solta e qualquer falha só
vira log. Um webhook lento ou fora do ar não pode segurar — nem derrubar — a
resposta do modelo que o originou.

Assinatura no header ``X-AIW-Signature: sha256=<hex>``, com ``X-AIW-Timestamp``
para o receptor rejeitar replays antigos.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import bg

logger = logging.getLogger(__name__)

EVENTS = (
    "key.created",
    "key.revoked",
    "limit.reached",
    "budget.alert",
    "request.error",
)

_TIMEOUT = 8.0


def is_public_webhook_url(url: str) -> bool:
    """Valida a forma do destino sem permitir hosts locais ou credenciais na URL."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        # Accessar ``port`` também valida portas malformadas (ex.: :99999).
        _ = parsed.port
    except ValueError:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


async def resolves_to_public_webhook_target(url: str) -> bool:
    """Recusa DNS que resolva para rede privada, loopback ou link-local."""
    if not is_public_webhook_url(url):
        return False
    parsed = urlsplit(url)
    try:
        rows = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError):
        return False
    try:
        return bool(rows) and all(ipaddress.ip_address(row[4][0]).is_global for row in rows)
    except (ValueError, IndexError):
        return False


def sign(secret: str, body: bytes, ts: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _enabled_for(key: Any, event: str) -> tuple[str, str] | None:
    hook = (getattr(key, "webhook", None) or {})
    url = str(hook.get("url") or "").strip()
    if not is_public_webhook_url(url):
        return None
    events = [str(e) for e in (hook.get("events") or [])]
    if events and event not in events:
        return None
    return url, str(hook.get("secret") or "")


async def _deliver(url: str, secret: str, payload: dict) -> None:
    if not await resolves_to_public_webhook_target(url):
        logger.warning("webhook recusado por destino não público: %s", url)
        return
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-AIW-Timestamp": ts,
               "X-AIW-Event": str(payload.get("event") or "")}
    if secret:
        headers["X-AIW-Signature"] = sign(secret, body, ts)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            await client.post(url, content=body, headers=headers)
    except Exception as exc:  # noqa: BLE001 - webhook do usuário; nunca propaga
        logger.warning("webhook %s falhou: %s", url, exc)


def emit(key: Any, event: str, data: dict | None = None) -> None:
    """Dispara o evento se a chave tiver webhook configurado para ele."""
    target = _enabled_for(key, event)
    if target is None:
        return
    url, secret = target
    payload = {
        "event": event,
        "created_at": int(time.time()),
        "api_key": {"id": str(key.id), "name": key.name},
        "data": data or {},
    }
    try:
        bg.spawn(_deliver(url, secret, payload), name="api-webhook")
    except RuntimeError:  # sem loop (script/teste): entrega síncrona, best-effort
        logger.debug("webhook %s sem loop rodando; ignorado", event)
