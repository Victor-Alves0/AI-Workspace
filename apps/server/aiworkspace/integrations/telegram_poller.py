"""Long-polling do Telegram: uma task asyncio por conexão habilitada.

Gerência de ciclo de vida (como o scheduler de automações): `start()` sobe uma task
por conexão habilitada; `refresh(id)` (re)inicia/para uma conexão quando o usuário
conecta/pausa/edita; `stop()` cancela tudo no shutdown. Cada loop segura o getUpdates
(long-poll) e despacha os updates para `telegram_service.handle_update`, persistindo
o offset para retomar após reinício.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from ..db import SessionLocal
from ..models import TelegramConnection
from . import telegram_api, telegram_service

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}


async def _loop(conn_id: uuid.UUID, token: str) -> None:
    """Loop de long-polling de UMA conexão. Persiste o offset a cada lote."""
    # garante long-polling (sem webhook pendente)
    try:
        await telegram_api.delete_webhook(token)
    except Exception:  # noqa: BLE001
        pass
    async with SessionLocal() as db:
        conn = await db.get(TelegramConnection, conn_id)
        offset = int(conn.update_offset or 0) if conn else 0
    backoff = 1.0
    while True:
        try:
            updates = await telegram_api.get_updates(token, offset, poll=25)
            backoff = 1.0
            if not updates:
                continue
            for u in updates:
                uid = int(u.get("update_id") or 0)
                if uid >= offset:
                    offset = uid + 1
                try:
                    await telegram_service.handle_update(conn_id, u)
                except Exception:  # noqa: BLE001
                    logger.exception("telegram: erro ao tratar update (%s)", conn_id)
            # persiste o offset avançado (confirma os updates ao servidor)
            async with SessionLocal() as db:
                c = await db.get(TelegramConnection, conn_id)
                if c is None or not c.enabled:
                    return
                c.update_offset = offset
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram poll (%s) erro: %s; retry em %.0fs", conn_id, exc, backoff)
            async with SessionLocal() as db:
                c = await db.get(TelegramConnection, conn_id)
                if c is None or not c.enabled:
                    return
                c.state = {**(c.state or {}), "last_error": str(exc)}
                await db.commit()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def _spawn(conn_id: uuid.UUID, token: str) -> None:
    key = str(conn_id)
    old = _tasks.pop(key, None)
    if old is not None and not old.done():
        old.cancel()
    _tasks[key] = asyncio.create_task(_loop(conn_id, token))


def _cancel(conn_id: uuid.UUID) -> None:
    t = _tasks.pop(str(conn_id), None)
    if t is not None and not t.done():
        t.cancel()


async def refresh(conn_id: uuid.UUID) -> None:
    """(Re)inicia ou para o poller de uma conexão conforme seu estado atual."""
    async with SessionLocal() as db:
        conn = await db.get(TelegramConnection, conn_id)
        if conn is None or not conn.enabled or not conn.bot_token:
            _cancel(conn_id)
            return
        token = conn.bot_token
    _spawn(conn_id, token)


async def start() -> None:
    """Sobe os pollers de todas as conexões habilitadas (chamado no lifespan)."""
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(TelegramConnection).where(TelegramConnection.enabled.is_(True))
        ))
        conns = [(c.id, c.bot_token) for c in rows if c.bot_token]
    for cid, token in conns:
        _spawn(cid, token)
    if conns:
        logger.info("telegram: %d poller(s) iniciado(s)", len(conns))


async def stop() -> None:
    for t in list(_tasks.values()):
        if not t.done():
            t.cancel()
    _tasks.clear()
