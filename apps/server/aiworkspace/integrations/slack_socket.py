"""Socket Mode do Slack (WebSocket): uma task asyncio por conexão habilitada.

Análogo ao `discord_gateway`, mas o transporte é o Socket Mode do Slack. Ciclo por
conexão: abre a URL via `apps.connections.open` (App-Level Token) → conecta ao WS →
recebe `hello` → recebe envelopes `events_api` → ACK de cada envelope (envia o
`envelope_id` de volta) e despacha os eventos `message` para
`slack_channel_service.handle_message`. Em `disconnect`, reabre a conexão.

Mesma API pública do discord_gateway: start()/refresh(id)/stop()/_spawn/_cancel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import websockets
from sqlalchemy import select

from ..db import SessionLocal
from ..models import SlackChannelConnection
from . import slack_channel_api, slack_channel_service

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}


async def _persist_state(conn_id: uuid.UUID, **patch: Any) -> None:
    async with SessionLocal() as db:
        c = await db.get(SlackChannelConnection, conn_id)
        if c is None:
            return
        c.state = {**(c.state or {}), **patch}
        await db.commit()


async def _session(ws, conn_id: uuid.UUID, bot_user_id: str) -> bool:
    """Uma sessão Socket Mode: recebe envelopes, faz ACK e despacha. Retorna
    `True` no `disconnect` (a conexão ANTIGA ainda está saudável — o chamador faz
    make-before-break) e `False` quando a conexão caiu (recv terminou/erro)."""
    async for raw in ws:
        try:
            env = json.loads(raw)
        except (ValueError, TypeError):
            continue
        etype = env.get("type")

        if etype == "hello":
            await _persist_state(conn_id, status="connected", last_error=None)
            continue
        if etype == "disconnect":
            return True  # Slack pediu p/ reabrir; a conexão atual segue viva no período de graça

        # todo envelope com envelope_id precisa de ACK (senão o Slack reenvia)
        env_id = env.get("envelope_id")
        if env_id:
            try:
                await ws.send(json.dumps({"envelope_id": env_id}))
            except Exception:  # noqa: BLE001 - conexão caiu; o recv trata
                return False

        if etype == "events_api":
            event = ((env.get("payload") or {}).get("event")) or {}
            if event.get("type") == "message":
                # não bloqueia o loop de recepção
                asyncio.create_task(_safe_handle(conn_id, event, bot_user_id))
    return False  # a conexão fechou


async def _safe_handle(conn_id: uuid.UUID, event: dict, bot_user_id: str) -> None:
    try:
        await slack_channel_service.handle_message(conn_id, event, bot_user_id)
    except Exception:  # noqa: BLE001
        logger.exception("slack: falha ao tratar mensagem (%s)", conn_id)


async def _close(ws) -> None:
    if ws is None:
        return
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


async def _loop(conn_id: uuid.UUID, bot_token: str, app_token: str, bot_user_id: str) -> None:
    """Loop de UMA conexão com reconexão MAKE-BEFORE-BREAK: no `disconnect` do Slack,
    abre a NOVA conexão antes de fechar a antiga — nunca fica sem conexão ativa (o
    Slack mantém a antiga no período de graça e aceita conexões simultâneas)."""
    backoff = 1.0
    ws = None  # conexão ativa atual (mantida viva enquanto a próxima não sobe)
    try:
        while True:
            # 1) abre a NOVA conexão (a antiga, se houver, segue aberta neste ponto)
            try:
                url = await slack_channel_api.open_socket_url(app_token)
                new_ws = await websockets.connect(url, max_size=2**22, open_timeout=30)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("slack socket (%s) erro ao abrir: %s; retry em %.0fs", conn_id, exc, backoff)
                await _persist_state(conn_id, last_error=str(exc)[:300])
                await asyncio.sleep(backoff)  # mantém a conexão antiga viva durante as retentativas
                backoff = min(backoff * 2, 60.0)
                continue

            # 2) nova de pé → adota-a como ATIVA e só então fecha a antiga
            #    (ws sempre aponta p/ a conexão viva antes do await, então o finally
            #    fecha a certa mesmo se cancelar durante o close)
            old, ws = ws, new_ws
            await _close(old)

            # 3) roda a sessão até `disconnect` (graceful) ou a conexão cair
            started = time.monotonic()
            try:
                graceful = await _session(ws, conn_id, bot_user_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("slack socket (%s) sessão caiu: %s", conn_id, exc)
                await _persist_state(conn_id, last_error=str(exc)[:300])
                graceful = False
            if not graceful:
                # conexão caiu: a antiga já está morta, não vale mantê-la
                await _close(ws)
                ws = None

            # 4) ainda deve rodar?
            async with SessionLocal() as db:
                c = await db.get(SlackChannelConnection, conn_id)
                if c is None or not c.enabled:
                    return

            # anti-busy-loop: sessão saudável (refresh a cada ~30min) zera o backoff;
            # sessão curtíssima indica problema → espera antes de reabrir
            if time.monotonic() - started >= 10.0:
                backoff = 1.0
            else:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
    finally:
        await _close(ws)


def _spawn(conn_id: uuid.UUID, bot_token: str, app_token: str, bot_user_id: str) -> None:
    key = str(conn_id)
    old = _tasks.pop(key, None)
    if old is not None and not old.done():
        old.cancel()
    _tasks[key] = asyncio.create_task(_loop(conn_id, bot_token, app_token, bot_user_id))


def _cancel(conn_id: uuid.UUID) -> None:
    t = _tasks.pop(str(conn_id), None)
    if t is not None and not t.done():
        t.cancel()


async def refresh(conn_id: uuid.UUID) -> None:
    """(Re)inicia ou para o socket de uma conexão conforme seu estado atual."""
    async with SessionLocal() as db:
        conn = await db.get(SlackChannelConnection, conn_id)
        if conn is None or not conn.enabled or not conn.bot_token or not conn.app_token:
            _cancel(conn_id)
            return
        bot_token, app_token, bot_user_id = conn.bot_token, conn.app_token, conn.bot_user_id
    _spawn(conn_id, bot_token, app_token, bot_user_id)


async def start() -> None:
    """Sobe os sockets de todas as conexões habilitadas (chamado no lifespan)."""
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(SlackChannelConnection).where(SlackChannelConnection.enabled.is_(True))
        ))
        conns = [(c.id, c.bot_token, c.app_token, c.bot_user_id)
                 for c in rows if c.bot_token and c.app_token]
    for cid, bt, at, buid in conns:
        _spawn(cid, bt, at, buid)
    if conns:
        logger.info("slack: %d socket(s) iniciado(s)", len(conns))


async def stop() -> None:
    for t in list(_tasks.values()):
        if not t.done():
            t.cancel()
    _tasks.clear()
