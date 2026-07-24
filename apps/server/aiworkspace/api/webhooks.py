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
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EVENTS = (
    "key.created",
    "key.revoked",
    "limit.reached",
    "budget.alert",
    "request.error",
)

_TIMEOUT = 8.0


def sign(secret: str, body: bytes, ts: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _enabled_for(key: Any, event: str) -> tuple[str, str] | None:
    hook = (getattr(key, "webhook", None) or {})
    url = str(hook.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    events = [str(e) for e in (hook.get("events") or [])]
    if events and event not in events:
        return None
    return url, str(hook.get("secret") or "")


async def _deliver(url: str, secret: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-AIW-Timestamp": ts,
               "X-AIW-Event": str(payload.get("event") or "")}
    if secret:
        headers["X-AIW-Signature"] = sign(secret, body, ts)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
        asyncio.get_running_loop().create_task(_deliver(url, secret, payload))
    except RuntimeError:  # sem loop (script/teste): entrega síncrona, best-effort
        logger.debug("webhook %s sem loop rodando; ignorado", event)
