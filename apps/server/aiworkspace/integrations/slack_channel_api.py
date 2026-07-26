"""Cliente REST fino da Web API do Slack para o CANAL (via httpx, async).

O recebimento é pelo Socket Mode (WebSocket, ver slack_socket); aqui o REST:
abrir a conexão Socket Mode (apps.connections.open, com o App-Level Token),
validar (auth.test), enviar (chat.postMessage) e resolver nomes (users.info).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://slack.com/api"
_MAX_LEN = 3900  # teto prático de uma mensagem do Slack (limite ~40k, quebramos bem antes)


class SlackChannelError(Exception):
    pass


async def _call(token: str, method: str, *, http: str = "GET",
                params: dict | None = None, data: dict | None = None) -> dict:
    url = f"{_BASE}/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        if http == "POST":
            headers["Content-Type"] = "application/json; charset=utf-8"
            r = await client.post(url, headers=headers, json=data)
        else:
            r = await client.get(url, headers=headers, params=params)
    try:
        j = r.json()
    except Exception as exc:  # noqa: BLE001
        raise SlackChannelError(f"resposta inválida ({r.status_code})") from exc
    if not j.get("ok"):
        raise SlackChannelError(j.get("error") or f"HTTP {r.status_code}")
    return j


async def auth_test(bot_token: str) -> dict[str, Any]:
    """Valida o Bot Token e devolve {team, team_id, user_id (bot), ...}."""
    j = await _call(bot_token, "auth.test", http="POST")
    return {"team": j.get("team") or "", "team_id": j.get("team_id") or "",
            "bot_user_id": j.get("user_id") or ""}


async def open_socket_url(app_token: str) -> str:
    """Abre uma conexão Socket Mode e devolve a URL WebSocket (wss)."""
    j = await _call(app_token, "apps.connections.open", http="POST")
    url = j.get("url")
    if not url:
        raise SlackChannelError("apps.connections.open não devolveu url")
    return url


async def post_message(bot_token: str, channel: str, text: str) -> dict[str, Any]:
    """Envia uma mensagem a um canal/DM. Quebra em blocos se muito longa."""
    text = text or ""
    if len(text) <= _MAX_LEN:
        return await _call(bot_token, "chat.postMessage", http="POST",
                           data={"channel": channel, "text": text})
    last: dict = {}
    for i in range(0, len(text), _MAX_LEN):
        last = await _call(bot_token, "chat.postMessage", http="POST",
                           data={"channel": channel, "text": text[i:i + _MAX_LEN]})
    return last


async def upload_file(bot_token: str, channel_id: str, data: bytes, filename: str,
                      caption: str = "") -> dict[str, Any]:
    """Envia um arquivo (imagem gerada) a um canal/DM. Fluxo de 3 passos do Slack
    (o `files.upload` antigo foi aposentado): reserva a URL, sobe os bytes, conclui e
    compartilha. Requer o escopo `files:write` no bot."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1) reserva a URL de upload (length = tamanho em bytes, obrigatório)
        r1 = await client.get(
            f"{_BASE}/files.getUploadURLExternal",
            headers={"Authorization": f"Bearer {bot_token}"},
            params={"filename": filename, "length": len(data)},
        )
        j1 = r1.json()
        if not j1.get("ok"):
            raise SlackChannelError(j1.get("error") or "getUploadURLExternal falhou")
        upload_url, file_id = j1["upload_url"], j1["file_id"]
        # 2) sobe os bytes (multipart) na URL reservada
        up = await client.post(upload_url, files={"file": (filename, data)})
        if up.status_code >= 400:
            raise SlackChannelError(f"upload dos bytes falhou (HTTP {up.status_code})")
        # 3) conclui e compartilha no canal
        payload: dict[str, Any] = {
            "files": [{"id": file_id, "title": filename}],
            "channel_id": channel_id,
        }
        if caption:
            payload["initial_comment"] = caption[:2000]
        r3 = await client.post(
            f"{_BASE}/files.completeUploadExternal",
            headers={"Authorization": f"Bearer {bot_token}",
                     "Content-Type": "application/json; charset=utf-8"},
            json=payload,
        )
    j3 = r3.json()
    if not j3.get("ok"):
        raise SlackChannelError(j3.get("error") or "completeUploadExternal falhou")
    return j3


async def user_name(bot_token: str, user_id: str) -> str:
    """Nome de exibição de um usuário (best-effort — vazio se faltar escopo)."""
    if not user_id:
        return ""
    try:
        j = await _call(bot_token, "users.info", params={"user": user_id})
    except SlackChannelError:
        return ""
    u = j.get("user") or {}
    prof = u.get("profile") or {}
    return prof.get("display_name") or u.get("real_name") or u.get("name") or ""
