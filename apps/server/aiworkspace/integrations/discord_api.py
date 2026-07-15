"""Cliente REST fino da API do Discord (via httpx).

Sem sidecar: falamos direto com discord.com/api. Cada chamada usa o token do bot da
conexão (header `Authorization: Bot <token>`). O recebimento de mensagens é pelo
Gateway (WebSocket, ver discord_gateway); aqui só o REST (validar token, enviar,
indicador de digitação, URL do gateway).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://discord.com/api/v10"


class DiscordError(Exception):
    pass


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


async def _call(token: str, method: str, path: str, *, timeout: float = 30.0, **json: Any) -> Any:
    url = f"{_BASE}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, url, headers=_auth(token),
                                 json={k: v for k, v in json.items() if v is not None} or None)
    if r.status_code == 204:
        return {}
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        raise DiscordError(f"resposta inválida ({r.status_code})") from exc
    if r.status_code >= 400:
        msg = (data.get("message") if isinstance(data, dict) else None) or f"HTTP {r.status_code}"
        raise DiscordError(msg)
    return data


async def get_me(token: str) -> dict[str, Any]:
    """Valida o token e devolve os dados do bot (id, username, ...)."""
    return await _call(token, "GET", "/users/@me", timeout=15.0)


async def get_gateway_url(token: str) -> str:
    """URL do Gateway (wss) recomendada para este bot."""
    data = await _call(token, "GET", "/gateway/bot", timeout=15.0)
    return (data or {}).get("url") or "wss://gateway.discord.gg"


# teto p/ tamanho de mensagem no Discord (2000 chars); quebramos acima disso
_MAX_LEN = 2000


async def send_message(token: str, channel_id: str, content: str) -> dict[str, Any]:
    """Envia uma mensagem a um canal/DM. Divide em blocos de 2000 chars se preciso."""
    content = content or ""
    if len(content) <= _MAX_LEN:
        return await _call(token, "POST", f"/channels/{channel_id}/messages", content=content)
    last: Any = {}
    for i in range(0, len(content), _MAX_LEN):
        last = await _call(token, "POST", f"/channels/{channel_id}/messages", content=content[i:i + _MAX_LEN])
    return last


async def send_file(token: str, channel_id: str, data: bytes, filename: str,
                    caption: str = "") -> dict[str, Any]:
    """Envia uma imagem como ANEXO (multipart) — o Discord renderiza inline no canal."""
    url = f"{_BASE}/channels/{channel_id}/messages"
    payload = {"content": caption[:2000]} if caption else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bot {token}"},  # sem Content-Type: o httpx põe o boundary
            data={"payload_json": json.dumps(payload)},
            files={"files[0]": (filename, data, "image/png")},
        )
    try:
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        raise DiscordError(f"resposta inválida ({r.status_code})") from exc
    if r.status_code >= 400:
        raise DiscordError((body.get("message") if isinstance(body, dict) else None)
                           or f"HTTP {r.status_code}")
    return body


async def get_messages(token: str, channel_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Lê as últimas mensagens de um canal/DM (o bot precisa ter acesso ao canal e o
    intent MESSAGE_CONTENT). Devolve [{from_me?, author, text, ts, msg_id}] em ordem
    cronológica. Requer o id do próprio bot p/ marcar 'from_me' — resolvido pelo caller."""
    url = f"{_BASE}/channels/{channel_id}/messages"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=_auth(token),
                              params={"limit": int(max(1, min(limit, 100)))})
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        raise DiscordError(f"resposta inválida ({r.status_code})") from exc
    if r.status_code >= 400:
        msg = (data.get("message") if isinstance(data, dict) else None) or f"HTTP {r.status_code}"
        raise DiscordError(msg)
    out: list[dict[str, Any]] = []
    for row in data if isinstance(data, list) else []:
        author = row.get("author") or {}
        out.append({
            "author": str(author.get("username") or author.get("global_name") or ""),
            "author_id": str(author.get("id") or ""),
            "is_bot": bool(author.get("bot")),
            "text": str(row.get("content") or ""),
            "ts": str(row.get("timestamp") or ""),
            "msg_id": str(row.get("id") or ""),
        })
    out.reverse()  # a API devolve mais recentes primeiro → ordem cronológica
    return out


async def trigger_typing(token: str, channel_id: str) -> None:
    try:
        await _call(token, "POST", f"/channels/{channel_id}/typing", timeout=10.0)
    except DiscordError:
        pass  # indicador de digitação é best-effort
