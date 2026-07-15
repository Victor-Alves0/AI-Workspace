"""Cliente fino da Bot API do Telegram (long-polling, via httpx).

Sem sidecar: falamos direto com api.telegram.org. Cada chamada usa o token do bot
da conexão. Erros da API viram TelegramError com a descrição.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    pass


async def _call(token: str, method: str, *, timeout: float = 30.0, **params: Any) -> Any:
    url = _BASE.format(token=token, method=method)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json={k: v for k, v in params.items() if v is not None})
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        raise TelegramError(f"resposta inválida ({r.status_code})") from exc
    if not data.get("ok"):
        raise TelegramError(data.get("description") or f"HTTP {r.status_code}")
    return data.get("result")


async def get_me(token: str) -> dict[str, Any]:
    """Valida o token e devolve os dados do bot (id, username, ...)."""
    return await _call(token, "getMe", timeout=15.0)


async def delete_webhook(token: str) -> None:
    """Garante long-polling: remove qualquer webhook antes de dar getUpdates."""
    try:
        await _call(token, "deleteWebhook", drop_pending_updates=False, timeout=15.0)
    except TelegramError as exc:
        logger.info("telegram deleteWebhook: %s", exc)


async def get_updates(token: str, offset: int, *, poll: int = 25) -> list[dict[str, Any]]:
    """Long-poll: o servidor segura até `poll` segundos aguardando updates novos.
    O `timeout` do Telegram vai no corpo; o timeout HTTP fica acima dele."""
    url = _BASE.format(token=token, method="getUpdates")
    body = {"offset": offset or None, "timeout": poll, "allowed_updates": ["message"]}
    async with httpx.AsyncClient(timeout=poll + 15.0) as client:
        r = await client.post(url, json={k: v for k, v in body.items() if v is not None})
    data = r.json()
    if not data.get("ok"):
        raise TelegramError(data.get("description") or f"HTTP {r.status_code}")
    return data.get("result") or []


async def send_message(token: str, chat_id: str, text: str) -> dict[str, Any]:
    return await _call(token, "sendMessage", chat_id=chat_id, text=text, timeout=30.0)


async def send_photo(token: str, chat_id: str, data: bytes, filename: str,
                     caption: str = "") -> dict[str, Any]:
    """Envia uma imagem (gráfico/imagem gerada) como FOTO — multipart, não JSON."""
    url = _BASE.format(token=token, method="sendPhoto")
    form = {"chat_id": chat_id}
    if caption:
        form["caption"] = caption[:1024]  # teto de legenda do Telegram
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, data=form, files={"photo": (filename, data, "image/png")})
    if r.status_code != 200:
        raise TelegramError(f"sendPhoto falhou (HTTP {r.status_code})")
    return r.json()


# teto p/ download de mídia (voz do Telegram raramente passa de poucos MB)
_MAX_FILE_BYTES = 20 * 1024 * 1024


async def get_file_bytes(token: str, file_id: str) -> bytes:
    """Baixa um arquivo do Telegram (nota de voz/áudio): getFile → download.
    Levanta TelegramError se indisponível ou grande demais."""
    info = await _call(token, "getFile", file_id=file_id, timeout=20.0)
    path = (info or {}).get("file_path") or ""
    if not path:
        raise TelegramError("arquivo sem file_path")
    if int((info or {}).get("file_size") or 0) > _MAX_FILE_BYTES:
        raise TelegramError("arquivo grande demais")
    url = f"https://api.telegram.org/file/bot{token}/{path}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(url)
    if r.status_code != 200:
        raise TelegramError(f"download falhou (HTTP {r.status_code})")
    return r.content


async def send_chat_action(token: str, chat_id: str, action: str = "typing") -> None:
    try:
        await _call(token, "sendChatAction", chat_id=chat_id, action=action, timeout=10.0)
    except TelegramError:
        pass  # indicador de digitação é best-effort
