"""Cliente da Evolution API — WhatsApp NÃO oficial (Baileys) via QR Code.

A Evolution roda como serviço do compose (opt-in: `--profile whatsapp`) e fala
com o WhatsApp Web pelo protocolo do Baileys. Nós só usamos o REST dela:
criar instância, obter QR, estado da sessão, enviar texto e apagar instância.
Os webhooks dela apontam de volta para o server (rede interna do compose).

Aviso honesto: integrações não oficiais violam os termos do WhatsApp e podem
levar ao banimento do número — a UI avisa; use um número descartável/secundário.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def configured() -> bool:
    """A Evolution está habilitada no deploy? (URL + chave no env)."""
    s = get_settings()
    return bool(s.evolution_api_url and s.evolution_api_key)


def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        base_url=s.evolution_api_url.rstrip("/"),
        headers={"apikey": s.evolution_api_key},
        timeout=_TIMEOUT,
    )


async def create_instance(instance: str, webhook_url: str) -> dict[str, Any]:
    """Cria a instância (sessão) e registra o webhook de mensagens recebidas."""
    async with _client() as c:
        r = await c.post(
            "/instance/create",
            json={
                "instanceName": instance,
                "integration": "WHATSAPP-BAILEYS",
                "qrcode": True,
                "webhook": {
                    "url": webhook_url,
                    "byEvents": False,
                    "base64": False,
                    "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
                },
            },
        )
        r.raise_for_status()
        return r.json()


async def get_qr(instance: str) -> dict[str, Any]:
    """QR atual da instância: {base64?: data-url, code?: str, pairingCode?: str}.
    Se a sessão já está aberta, o Evolution devolve o estado em vez do QR."""
    async with _client() as c:
        r = await c.get(f"/instance/connect/{instance}")
        r.raise_for_status()
        return r.json()


async def get_state(instance: str) -> str:
    """"open" (conectado) | "connecting" | "close"."""
    async with _client() as c:
        r = await c.get(f"/instance/connectionState/{instance}")
        r.raise_for_status()
        data = r.json()
        return ((data.get("instance") or {}).get("state")) or data.get("state") or "close"


async def get_profile(instance: str) -> dict[str, str]:
    """{phone, name} da sessão conectada (via fetchInstances). Best-effort."""
    try:
        async with _client() as c:
            r = await c.get("/instance/fetchInstances", params={"instanceName": instance})
            r.raise_for_status()
            data = r.json()
        items = data if isinstance(data, list) else [data]
        for it in items:
            row = it.get("instance") or it  # formato varia entre versões
            name = row.get("instanceName") or row.get("name") or ""
            if name != instance:
                continue
            owner = row.get("owner") or row.get("ownerJid") or ""
            return {
                "phone": owner.split("@")[0] if owner else "",
                "name": row.get("profileName") or "",
            }
    except Exception as exc:  # noqa: BLE001
        logger.debug("fetchInstances falhou: %s", exc)
    return {"phone": "", "name": ""}


async def send_text(instance: str, jid: str, text: str) -> dict[str, Any]:
    """Envia texto para um contato/grupo. `jid` pode ser o jid completo ou só dígitos."""
    async with _client() as c:
        r = await c.post(
            f"/message/sendText/{instance}",
            json={"number": jid, "text": text},
        )
        r.raise_for_status()
        return r.json()


async def delete_instance(instance: str) -> None:
    """Desconecta e apaga a instância (best-effort; usada ao excluir a conexão)."""
    try:
        async with _client() as c:
            await c.delete(f"/instance/logout/{instance}")
            await c.delete(f"/instance/delete/{instance}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("falha ao apagar instância %s no Evolution: %s", instance, exc)


def parse_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza um webhook MESSAGES_UPSERT do Evolution em mensagens de texto:
    [{jid, text, sender_name, from_me, is_group, msg_id}]. Ignora não-texto."""
    if (payload.get("event") or "").replace(".", "_").lower() != "messages_upsert":
        return []
    data = payload.get("data") or {}
    items = data if isinstance(data, list) else [data]
    out: list[dict[str, Any]] = []
    for it in items:
        key = it.get("key") or {}
        jid = key.get("remoteJid") or ""
        msg = it.get("message") or {}
        text = (
            msg.get("conversation")
            or (msg.get("extendedTextMessage") or {}).get("text")
            or ""
        )
        if not jid or not str(text).strip():
            continue
        out.append(
            {
                "jid": jid,
                # em grupos, quem falou é o participant (p/ filtros por contato)
                "sender": key.get("participant") or jid,
                "text": str(text).strip(),
                "sender_name": it.get("pushName") or "",
                "from_me": bool(key.get("fromMe")),
                "is_group": jid.endswith("@g.us"),
                "msg_id": key.get("id") or "",
            }
        )
    return out
