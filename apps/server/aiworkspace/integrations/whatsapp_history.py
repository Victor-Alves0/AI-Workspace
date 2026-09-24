"""Histórico do WhatsApp embutido (whatsmeow): conversas e mensagens no Postgres.

O whatsmeow não guarda histórico nem a lista de conversas. Sem isto, "ler a conversa"
e "listar conversas" só enxergavam o que chegou desde o último restart. Aqui entra
tudo o que passa pela sessão: o que chega, o que sai (pelo app ou pelo próprio
celular) e o histórico que o WhatsApp manda ao parear. Mídia guarda a mensagem
protobuf serializada (chaves + URL), o que basta para baixá-la depois.

Linhas no formato de `whatsapp_local.normalize`, mais `kind` e `media` (bytes|None).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import WhatsAppChat, WhatsAppConnection, WhatsAppMessage

logger = logging.getLogger(__name__)

_IDS: dict[str, uuid.UUID] = {}   # instância → id da conexão (o nome é único e fixo)
_LOTE = 500                       # linhas por INSERT (o histórico do pareamento é grande)


async def connection_id(instance: str) -> uuid.UUID | None:
    if instance in _IDS:
        return _IDS[instance]
    async with SessionLocal() as db:
        cid = await db.scalar(select(WhatsAppConnection.id).where(WhatsAppConnection.instance == instance))
    if cid is not None:
        _IDS[instance] = cid
    return cid


def forget(instance: str) -> None:
    _IDS.pop(instance, None)


def _quando(ts: Any) -> datetime:
    try:
        n = int(ts or 0)
    except (TypeError, ValueError):
        n = 0
    return datetime.fromtimestamp(n, timezone.utc) if n > 0 else datetime.now(timezone.utc)


async def record(instance: str, rows: list[dict[str, Any]],
                 names: dict[str, tuple[str, bool]] | None = None) -> int:
    """Grava mensagens (repetidas são ignoradas) e atualiza as conversas.
    `names`: jid → (nome, é_grupo) conhecido da conversa. Devolve quantas entraram.
    Nunca levanta: histórico é auxiliar, não pode derrubar a entrega da mensagem."""
    try:
        cid = await connection_id(instance)
        if cid is None:
            return 0
        msgs = [
            {
                "id": uuid.uuid4(), "connection_id": cid, "jid": r["jid"], "msg_id": r["msg_id"],
                "from_me": bool(r.get("from_me")), "sender": (r.get("sender") or "")[:128],
                "sender_name": (r.get("sender_name") or "")[:255], "text": r.get("text") or "",
                "kind": r.get("kind") or "text", "sent_at": _quando(r.get("ts")), "media": r.get("media"),
            }
            for r in rows if r.get("jid") and r.get("msg_id")
        ]
        conversas: dict[str, dict[str, Any]] = {}
        for m in msgs:
            c = conversas.setdefault(m["jid"], {"last": m["sent_at"], "name": "", "group": m["jid"].endswith("@g.us")})
            c["last"] = max(c["last"], m["sent_at"])
            if not c["group"] and not m["from_me"] and m["sender_name"]:
                c["name"] = m["sender_name"]
        for jid, (nome, grupo) in (names or {}).items():
            c = conversas.setdefault(jid, {"last": None, "name": "", "group": grupo})
            c["name"], c["group"] = nome or c["name"], grupo

        entraram = 0
        async with SessionLocal() as db:
            for i in range(0, len(msgs), _LOTE):
                res = await db.execute(
                    insert(WhatsAppMessage).values(msgs[i:i + _LOTE])
                    .on_conflict_do_nothing(constraint="uq_whatsapp_message_id")
                    .returning(WhatsAppMessage.id)
                )
                entraram += len(res.all())
            for jid, c in conversas.items():
                stmt = insert(WhatsAppChat).values(
                    id=uuid.uuid4(), connection_id=cid, jid=jid, name=c["name"][:255],
                    is_group=c["group"], last_message_at=c["last"],
                )
                novos = {
                    "last_message_at": func.greatest(WhatsAppChat.last_message_at, stmt.excluded.last_message_at),
                    "updated_at": func.now(),
                }
                if c["name"]:
                    novos["name"] = stmt.excluded.name
                await db.execute(stmt.on_conflict_do_update(constraint="uq_whatsapp_chat_jid", set_=novos))
            await db.commit()
        return entraram
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp: histórico não gravado (%s): %s", instance, exc)
        return 0


async def chats(instance: str, limit: int = 50) -> list[dict[str, Any]]:
    """Conversas, da mais recente para a mais antiga."""
    cid = await connection_id(instance)
    if cid is None:
        return []
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(WhatsAppChat).where(WhatsAppChat.connection_id == cid)
            .order_by(WhatsAppChat.last_message_at.desc().nulls_last()).limit(max(1, limit))
        ))
    return [{"jid": c.jid, "name": c.name or "", "is_group": c.is_group} for c in rows]


async def unnamed_groups(instance: str) -> list[str]:
    cid = await connection_id(instance)
    if cid is None:
        return []
    async with SessionLocal() as db:
        return list(await db.scalars(
            select(WhatsAppChat.jid).where(WhatsAppChat.connection_id == cid,
                                           WhatsAppChat.is_group.is_(True), WhatsAppChat.name == "")
        ))


async def messages(instance: str, jid: str, limit: int = 20) -> list[dict[str, Any]]:
    """As últimas `limit` mensagens da conversa, em ordem cronológica."""
    cid = await connection_id(instance)
    if cid is None:
        return []
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(WhatsAppMessage).where(WhatsAppMessage.connection_id == cid, WhatsAppMessage.jid == jid)
            .order_by(WhatsAppMessage.sent_at.desc()).limit(max(1, limit))
        ))
    return [
        {"from_me": m.from_me, "text": m.text or (f"[{m.kind}]" if m.kind != "text" else ""),
         "sender_name": m.sender_name, "ts": int(m.sent_at.timestamp()), "msg_id": m.msg_id,
         "kind": m.kind}
        for m in reversed(rows)
    ]


async def media(instance: str, msg_id: str) -> bytes | None:
    """A mensagem protobuf serializada de uma mídia (para baixar), se houver."""
    cid = await connection_id(instance)
    if cid is None or not msg_id:
        return None
    async with SessionLocal() as db:
        return await db.scalar(
            select(WhatsAppMessage.media).where(WhatsAppMessage.connection_id == cid,
                                                WhatsAppMessage.msg_id == msg_id,
                                                WhatsAppMessage.media.isnot(None)).limit(1)
        )
