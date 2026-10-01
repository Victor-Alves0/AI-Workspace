"""Agência de mensagens — a IA age nas conexões de chat do usuário (WhatsApp,
Telegram, Discord) a pedido dele: lista conversas, lê o que foi dito e envia
mensagens ("responda o fulano por mim", "avisa no grupo", "vê o que ele disse").

Isto é a camada de SAÍDA ativa, distinta do loop de ENTRADA de cada canal
(telegram/discord/whatsapp_service, que respondem quando alguém fala com o bot).
Aqui a *tool* `messaging.chat.manage` (ver tools/sift_service) chama estas funções.

Identidade importa e difere por plataforma — a tool expõe isso honestamente:
- WhatsApp (Evolution): pareado ao número REAL do usuário → age COMO o usuário.
  list/read/send completos.
- Telegram (bot) e Discord (bot): identidade separada (o bot). Alcança só as
  conversas que o bot conhece; o Telegram-bot NÃO lê histórico livre (limite da
  API), o Discord-bot lê canais em que está. Nada de userbot/selfbot aqui.

As funções abrem a própria sessão de banco (são chamadas do sandbox síncrono do
SIFT via asyncio.run, como github_service.get_token) e resolvem o token/instância
ao vivo a partir da conexão — nada de segredo na config da tool.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..models import (
    DiscordConnection,
    DiscordThread,
    TelegramConnection,
    TelegramThread,
    WhatsAppChat,
    WhatsAppConnection,
    WhatsAppMessage,
    WhatsAppThread,
)
from . import discord_api, telegram_api, whatsapp_official as official
from . import whatsapp_qr as evolution

logger = logging.getLogger(__name__)

PLATFORMS = ("whatsapp", "telegram", "discord")

_CONN_MODEL = {
    "whatsapp": WhatsAppConnection,
    "telegram": TelegramConnection,
    "discord": DiscordConnection,
}


class MessagingError(Exception):
    """Erro amigável já pronto p/ devolver ao modelo."""


# --------------------------------------------------------------------------- #
# Catálogo de conexões (para o loader montar a config por-modelo)
# --------------------------------------------------------------------------- #
async def gather_accounts(db, user_id: uuid.UUID) -> list[dict[str, str]]:
    """Conexões de chat ATIVAS do usuário, achatadas p/ [{id, platform, label}].
    O loader filtra pelas liberadas no modelo; a tool usa isto p/ escolher o alvo."""
    out: list[dict[str, str]] = []
    for platform, model in _CONN_MODEL.items():
        rows = list(await db.scalars(
            select(model).where(model.user_id == user_id, model.enabled.is_(True))
        ))
        for c in rows:
            label = c.label or getattr(c, "bot_username", "") or getattr(c, "instance", "") or platform
            out.append({"id": str(c.id), "platform": platform, "label": label})
    return out


def _to_wa_jid(chat: str) -> str:
    """Aceita jid completo, número com dígitos, ou grupo. Converte dígitos → jid."""
    c = (chat or "").strip()
    if "@" in c:
        return c
    digits = "".join(ch for ch in c if ch.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else ""


async def _load(platform: str, connection_id: str):
    model = _CONN_MODEL.get(platform)
    if model is None:
        raise MessagingError(f"plataforma '{platform}' desconhecida.")
    async with SessionLocal() as db:
        conn = await db.get(model, uuid.UUID(str(connection_id)))
        if conn is None:
            raise MessagingError("conexão não encontrada (reconecte em Integrações).")
        if not conn.enabled:
            raise MessagingError(f"a conexão {getattr(conn, 'label', '') or platform} está pausada.")
        # dispara a descriptografia dos campos cifrados dentro da sessão
        _ = getattr(conn, "bot_token", "") or getattr(conn, "access_token", "")
        db.expunge(conn)
        return conn


# --------------------------------------------------------------------------- #
# list_chats
# --------------------------------------------------------------------------- #
async def list_chats(platform: str, connection_id: str, query: str = "", limit: int = 30) -> list[dict[str, Any]]:
    conn = await _load(platform, connection_id)
    q = (query or "").strip().lower()

    if platform == "whatsapp":
        if conn.provider != "evolution":
            # Cloud API oficial não lista conversas: as que passaram por aqui ficam no
            # histórico do app (e as respondidas, nas threads)
            from . import whatsapp_history
            seen = await whatsapp_history.chats_for(conn.id, limit=max(limit, 50))
            known = await _known_threads(WhatsAppThread, connection_id, "jid", q, limit)
            ids = {c["jid"] for c in seen}
            rows = [{"id": c["jid"], "name": c["name"], "is_group": False} for c in seen
                    if not q or q in c["name"].lower() or q in c["jid"].lower()]
            rows += [k for k in known if k["id"] not in ids]
            return rows[:limit]
        chats = await evolution.find_chats(conn.instance, limit=max(limit, 50))
        if q:
            contacts = await evolution.find_contacts(conn.instance, query=q, limit=limit)
            seen = {c["jid"] for c in chats}
            chats += [c for c in contacts if c["jid"] not in seen]
            chats = [c for c in chats if q in (c.get("name", "").lower()) or q in c["jid"].lower()]
        return [{"id": c["jid"], "name": c.get("name", ""), "is_group": c.get("is_group", False)}
                for c in chats[:limit]]

    if platform == "telegram":
        return await _known_threads(TelegramThread, connection_id, "tg_chat_id", q, limit, group_attr="is_group")

    # discord
    return await _known_threads(DiscordThread, connection_id, "channel_id", q, limit, dm_attr="is_dm")


async def _known_threads(model, connection_id: str, id_attr: str, q: str, limit: int,
                         group_attr: str = "", dm_attr: str = "") -> list[dict[str, Any]]:
    """Conversas que o BOT já conhece (Telegram/Discord/WhatsApp-oficial não listam
    contatos arbitrários — só quem interagiu)."""
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(model).where(model.connection_id == uuid.UUID(str(connection_id)))
        ))
    out: list[dict[str, Any]] = []
    for t in rows:
        name = t.contact_name or ""
        cid = str(getattr(t, id_attr) or "")
        if q and q not in name.lower() and q not in cid.lower():
            continue
        item: dict[str, Any] = {"id": cid, "name": name}
        if group_attr:
            item["is_group"] = bool(getattr(t, group_attr, False))
        if dm_attr:
            item["is_dm"] = bool(getattr(t, dm_attr, False))
        out.append(item)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# read_messages
# --------------------------------------------------------------------------- #
async def read_messages(platform: str, connection_id: str, chat: str, limit: int = 20) -> list[dict[str, Any]]:
    conn = await _load(platform, connection_id)
    if not (chat or "").strip():
        raise MessagingError("informe a conversa (`chat`) a ler.")

    if platform == "whatsapp":
        if conn.provider != "evolution":
            # Cloud API: o histórico é o que o app gravou (só o número, sem @...)
            from . import whatsapp_history
            msgs = await whatsapp_history.messages_for(
                conn.id, "".join(ch for ch in chat if ch.isdigit()), limit=limit)
        else:
            msgs = await evolution.find_messages(conn.instance, _to_wa_jid(chat), limit=limit)
        return [{"from": "você" if m["from_me"] else (m["sender_name"] or "contato"),
                 "text": m["text"], "from_me": m["from_me"], "ts": m["ts"]} for m in msgs]

    if platform == "telegram":
        raise MessagingError(
            "o Telegram (bot) não permite ler o histórico de uma conversa — a API de bots "
            "só entrega mensagens novas. Eu respondo quando a mensagem chega."
        )

    # discord — o bot lê canais em que está (intent Message Content)
    try:
        msgs = await discord_api.get_messages(conn.bot_token, chat, limit=limit)
    except discord_api.DiscordError as exc:
        raise MessagingError(str(exc)) from exc
    me = str(getattr(conn, "app_id", "") or "")
    return [{"from": m["author"] or "alguém", "text": m["text"],
             "from_me": bool(me and m.get("author_id") == me), "ts": m["ts"]} for m in msgs]


# --------------------------------------------------------------------------- #
# send_message
# --------------------------------------------------------------------------- #
async def send_message(platform: str, connection_id: str, chat: str, text: str) -> dict[str, Any]:
    conn = await _load(platform, connection_id)
    text = (text or "").strip()
    target = (chat or "").strip()
    if not target:
        raise MessagingError("informe o destinatário (`chat`).")
    if not text:
        raise MessagingError("a mensagem está vazia.")

    if platform == "whatsapp":
        jid = _to_wa_jid(target)
        if not jid:
            raise MessagingError("destinatário inválido (informe o número com DDD ou o jid).")
        if conn.provider == "official":
            await official.send_text(conn.phone_number_id, conn.access_token, jid, text)
            from . import whatsapp_history
            await whatsapp_history.record_for(conn.id, [{
                "jid": jid.split("@")[0], "msg_id": f"out-{uuid.uuid4().hex}", "from_me": True,
                "text": text, "ts": int(time.time())}], label=conn.label)
        else:
            await evolution.send_text(conn.instance, jid, text)
        return {"ok": True, "to": jid, "platform": platform}

    if platform == "telegram":
        await telegram_api.send_message(conn.bot_token, target, text)
        return {"ok": True, "to": target, "platform": platform}

    # discord
    try:
        await discord_api.send_message(conn.bot_token, target, text)
    except discord_api.DiscordError as exc:
        raise MessagingError(str(exc)) from exc
    return {"ok": True, "to": target, "platform": platform}


# --------------------------------------------------------------------------- #
# new_messages — "chegou mensagem nova?"
# --------------------------------------------------------------------------- #
async def new_messages(platform: str, connection_id: str, since_hours: float = 24,
                       unanswered_only: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    """Conversas com mensagens RECEBIDAS nas últimas `since_hours`, da mais recente
    para a mais antiga. `unanswered` = a última mensagem da conversa é do contato
    (ninguém respondeu ainda). Só o WhatsApp guarda o que chega; os bots do
    Telegram/Discord respondem na hora e não têm "caixa de entrada"."""
    conn = await _load(platform, connection_id)
    if platform != "whatsapp":
        raise MessagingError(f"ver mensagens novas só está disponível no WhatsApp (não no {platform}).")
    since = datetime.now(timezone.utc) - timedelta(hours=max(0.1, float(since_hours or 24)))
    from . import whatsapp_qr as qr
    if conn.provider == "evolution" and qr.backend() != "local":
        return await _new_from_evolution(conn, since, unanswered_only, limit)

    cid = uuid.UUID(str(connection_id))
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.connection_id == cid, WhatsAppMessage.sent_at >= since)
            .order_by(WhatsAppMessage.sent_at.desc()).limit(500)
        ))
        jids = list({r.jid for r in rows})
        names = dict((await db.execute(
            select(WhatsAppChat.jid, WhatsAppChat.name)
            .where(WhatsAppChat.connection_id == cid, WhatsAppChat.jid.in_(jids))
        )).all()) if jids else {}
    convos: dict[str, list] = {}
    for r in rows:  # mais recentes primeiro
        convos.setdefault(r.jid, []).append(r)
    out: list[dict[str, Any]] = []
    for jid, msgs in convos.items():
        incoming = [m for m in msgs if not m.from_me]
        if not incoming:
            continue
        unanswered = not msgs[0].from_me
        if unanswered_only and not unanswered:
            continue
        out.append({
            "chat": jid,
            "name": names.get(jid) or incoming[0].sender_name or "",
            "is_group": jid.endswith("@g.us"),
            "unanswered": unanswered,
            "last_ts": int(msgs[0].sent_at.timestamp()),
            "messages": [
                {"from": m.sender_name or "contato",
                 "text": m.text or (f"[{m.kind}]" if m.kind != "text" else ""),
                 "ts": int(m.sent_at.timestamp())}
                for m in reversed(incoming[:5])
            ],
        })
        if len(out) >= limit:
            break
    return out


async def _new_from_evolution(conn, since: datetime, unanswered_only: bool, limit: int) -> list[dict[str, Any]]:
    """Evolution externa: o histórico mora nela — varre as conversas mais recentes."""
    out: list[dict[str, Any]] = []
    cut = int(since.timestamp())
    for c in (await evolution.find_chats(conn.instance, limit=15))[:15]:
        msgs = await evolution.find_messages(conn.instance, c["jid"], limit=10)
        recent = [m for m in msgs if int(m.get("ts") or 0) >= cut]
        incoming = [m for m in recent if not m["from_me"]]
        if not incoming:
            continue
        unanswered = not recent[-1]["from_me"]
        if unanswered_only and not unanswered:
            continue
        out.append({
            "chat": c["jid"], "name": c.get("name", ""), "is_group": c.get("is_group", False),
            "unanswered": unanswered, "last_ts": int(recent[-1].get("ts") or 0),
            "messages": [{"from": m["sender_name"] or "contato", "text": m["text"], "ts": m["ts"]}
                         for m in incoming[-5:]],
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x["last_ts"], reverse=True)
    return out
