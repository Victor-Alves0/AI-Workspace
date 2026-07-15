"""Gateway do Discord (WebSocket): uma task asyncio por conexão habilitada.

Análogo ao `telegram_poller`, mas o transporte é um WebSocket persistente (não
long-poll). Ciclo por conexão: conecta ao Gateway → recebe HELLO → dispara o
heartbeat periódico → envia IDENTIFY (ou RESUME, se há sessão) → recebe eventos
(op 0) e despacha MESSAGE_CREATE para `discord_service.handle_message`. Guarda
session_id/seq/resume_url em `conn.state` p/ retomar (RESUME) após reconexão.

Mesma API pública do telegram_poller: start()/refresh(id)/stop()/_spawn/_cancel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any

import websockets
from sqlalchemy import select

from ..db import SessionLocal
from ..models import DiscordConnection
from . import discord_api, discord_service

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}

# intents: GUILDS(1<<0) | GUILD_MESSAGES(1<<9) | DIRECT_MESSAGES(1<<12) |
# MESSAGE_CONTENT(1<<15) — precisamos do conteúdo p/ responder.
_INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)

# opcodes do Gateway
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11


async def _persist_state(conn_id: uuid.UUID, **patch: Any) -> None:
    async with SessionLocal() as db:
        c = await db.get(DiscordConnection, conn_id)
        if c is None:
            return
        c.state = {**(c.state or {}), **patch}
        await db.commit()


async def _heartbeat(ws, interval: float, sess: dict) -> None:
    """Envia op 1 a cada `interval` segundos (com jitter no 1º), carregando o seq."""
    await asyncio.sleep(interval * random.random())
    while True:
        try:
            await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": sess.get("seq")}))
        except Exception:  # noqa: BLE001 - conexão caiu; o loop de recv trata
            return
        await asyncio.sleep(interval)


async def _session(ws, conn_id: uuid.UUID, token: str, sess: dict) -> None:
    """Uma sessão de Gateway: handshake + loop de recepção. Retorna quando a conexão
    fecha (o chamador reconecta). Levanta em erro fatal."""
    hb_task: asyncio.Task | None = None
    try:
        async for raw in ws:
            payload = json.loads(raw)
            op = payload.get("op")
            if payload.get("s") is not None:
                sess["seq"] = payload["s"]

            if op == OP_HELLO:
                interval = float(payload["d"]["heartbeat_interval"]) / 1000.0
                hb_task = asyncio.create_task(_heartbeat(ws, interval, sess))
                if sess.get("session_id") and sess.get("seq") is not None:
                    await ws.send(json.dumps({
                        "op": OP_RESUME,
                        "d": {"token": token, "session_id": sess["session_id"], "seq": sess["seq"]},
                    }))
                else:
                    await ws.send(json.dumps({
                        "op": OP_IDENTIFY,
                        "d": {
                            "token": token,
                            "intents": _INTENTS,
                            "properties": {"os": "linux", "browser": "aiworkspace", "device": "aiworkspace"},
                        },
                    }))
            elif op == OP_HEARTBEAT:
                await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": sess.get("seq")}))
            elif op == OP_RECONNECT:
                return  # reconecta mantendo a sessão (RESUME)
            elif op == OP_INVALID_SESSION:
                sess["session_id"] = None  # sessão não pode ser retomada → IDENTIFY novo
                await asyncio.sleep(1.0 + random.random() * 4)
                return
            elif op == OP_DISPATCH:
                t = payload.get("t")
                if t == "READY":
                    d = payload.get("d") or {}
                    sess["session_id"] = d.get("session_id")
                    sess["resume_url"] = d.get("resume_gateway_url")
                    await _persist_state(
                        conn_id, status="connected", last_error=None,
                        session_id=sess["session_id"], resume_url=sess["resume_url"], seq=sess.get("seq"),
                    )
                elif t == "RESUMED":
                    await _persist_state(conn_id, status="connected", last_error=None)
                elif t == "MESSAGE_CREATE":
                    # não bloqueia o loop (heartbeat precisa fluir): dispara em task
                    asyncio.create_task(_safe_handle(conn_id, payload.get("d") or {}))
    finally:
        if hb_task is not None and not hb_task.done():
            hb_task.cancel()


async def _safe_handle(conn_id: uuid.UUID, event: dict) -> None:
    try:
        await discord_service.handle_message(conn_id, event)
    except Exception:  # noqa: BLE001
        logger.exception("discord: falha ao tratar mensagem (%s)", conn_id)


async def _loop(conn_id: uuid.UUID, token: str) -> None:
    """Loop de conexão de UMA conexão: (re)conecta ao Gateway e mantém a sessão."""
    async with SessionLocal() as db:
        conn = await db.get(DiscordConnection, conn_id)
        st = (conn.state or {}) if conn else {}
    sess: dict[str, Any] = {
        "session_id": st.get("session_id"),
        "seq": st.get("seq"),
        "resume_url": st.get("resume_url"),
    }
    backoff = 1.0
    while True:
        try:
            base = sess.get("resume_url") if sess.get("session_id") else None
            if not base:
                base = await discord_api.get_gateway_url(token)
            url = f"{base}?v=10&encoding=json"
            async with websockets.connect(url, max_size=2**22, open_timeout=30) as ws:
                backoff = 1.0
                await _session(ws, conn_id, token, sess)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("discord gateway (%s) erro: %s; retry em %.0fs", conn_id, exc, backoff)
            await _persist_state(conn_id, last_error=str(exc)[:300])
            # se a sessão parece inválida, força IDENTIFY novo na próxima
            sess["resume_url"] = sess.get("resume_url")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        # fechamento limpo (RECONNECT/INVALID_SESSION): reconecta rápido
        # (verifica se ainda deve rodar)
        async with SessionLocal() as db:
            c = await db.get(DiscordConnection, conn_id)
            if c is None or not c.enabled:
                return
        await asyncio.sleep(1.0)


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
    """(Re)inicia ou para o gateway de uma conexão conforme seu estado atual."""
    async with SessionLocal() as db:
        conn = await db.get(DiscordConnection, conn_id)
        if conn is None or not conn.enabled or not conn.bot_token:
            _cancel(conn_id)
            return
        token = conn.bot_token
    _spawn(conn_id, token)


async def start() -> None:
    """Sobe os gateways de todas as conexões habilitadas (chamado no lifespan)."""
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(DiscordConnection).where(DiscordConnection.enabled.is_(True))
        ))
        conns = [(c.id, c.bot_token) for c in rows if c.bot_token]
    for cid, token in conns:
        _spawn(cid, token)
    if conns:
        logger.info("discord: %d gateway(s) iniciado(s)", len(conns))


async def stop() -> None:
    for t in list(_tasks.values()):
        if not t.done():
            t.cancel()
    _tasks.clear()
