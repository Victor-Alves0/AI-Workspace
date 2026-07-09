"""WhatsApp Cloud API (Meta) — o caminho OFICIAL, nativo (sem sidecar).

O usuário cria um app no Meta for Developers, pega o token permanente + phone
number id e registra o webhook (a UI mostra a URL + verify token para colar no
console). Exige que o server seja alcançável publicamente por HTTPS (túnel /
proxy reverso) — a Meta só entrega webhooks em URL pública.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com/v20.0"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def send_text(phone_number_id: str, access_token: str, to: str, text: str) -> dict[str, Any]:
    """Envia texto pelo Cloud API. `to` = número em dígitos (E.164 sem '+')."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(
            f"{GRAPH_BASE}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.split("@")[0],
                "type": "text",
                "text": {"body": text[:4096]},
            },
        )
        r.raise_for_status()
        return r.json()


def valid_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Valida o X-Hub-Signature-256 da Meta. Sem app_secret configurado, aceita
    (a URL já carrega um token aleatório próprio)."""
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def parse_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza o webhook do Cloud API em mensagens de texto:
    [{jid, text, sender_name, from_me, is_group, msg_id}]."""
    out: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {
                (ct.get("wa_id") or ""): ((ct.get("profile") or {}).get("name") or "")
                for ct in value.get("contacts") or []
            }
            for m in value.get("messages") or []:
                if m.get("type") != "text":
                    continue
                sender = m.get("from") or ""
                text = ((m.get("text") or {}).get("body") or "").strip()
                if not sender or not text:
                    continue
                out.append(
                    {
                        "jid": sender,  # Cloud API entrega só o número (sem @...)
                        "text": text,
                        "sender_name": names.get(sender, ""),
                        "from_me": False,  # o Cloud API não repassa as próprias
                        "is_group": False,  # Cloud API é 1:1 (grupos não suportados)
                        "msg_id": m.get("id") or "",
                    }
                )
    return out
