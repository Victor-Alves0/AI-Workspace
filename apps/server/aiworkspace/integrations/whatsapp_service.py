"""Pipeline do WhatsApp: webhook → filtros → chat/thread → modelo → resposta.

`handle_incoming` é chamado pelas rotas de webhook (Evolution ou Cloud API) com
as mensagens já normalizadas ({jid, text, sender, ...}). Cada conversa (jid)
vira um Chat normal do app (visível na sidebar); o turno roda como nas
automações (background=True, sessão própria), e a resposta volta pelo provedor.

Concorrência: um lock por conversa serializa turnos — mensagens que chegam
durante uma resposta esperam a vez (ordem preservada, sem respostas cruzadas).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..models import (
    Chat,
    Message,
    ModelConfig,
    User,
    WhatsAppConnection,
    WhatsAppThread,
)
from ..usage_service import usage_event_from_record
from . import whatsapp_evolution as evolution
from . import whatsapp_official as official

logger = logging.getLogger(__name__)


def _err_text(exc: BaseException) -> str:
    """str(exc) legível — timeouts do httpx têm str vazia."""
    return str(exc).strip() or type(exc).__name__

# lock por conversa (conexão+jid). Vive na memória do processo; vazamento é
# desprezível (um Lock por contato ativo).
_locks: dict[str, asyncio.Lock] = {}


def _lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _digits(value: str) -> str:
    return re.sub(r"\D", "", (value or "").split("@")[0])


def _norm_br(digits: str) -> str:
    """Normaliza números BR: o WhatsApp costuma usar o formato SEM o nono dígito
    (55 + DDD + 8 dígitos), mas as pessoas digitam COM o 9. Remove o 9 extra para
    comparar as duas formas ("5583991659211" ≡ "558391659211")."""
    return re.sub(r"^55(\d{2})9(\d{8})$", r"55\1\2", digits)


def passes_filters(conn: WhatsAppConnection, m: dict[str, Any]) -> tuple[bool, str]:
    """Camada de filtragem ANTES do modelo. Retorna (aprovada, motivo_recusa)."""
    f = conn.filters or {}
    if m.get("from_me"):
        return False, "mensagem própria"
    if m.get("is_group") and not f.get("groups"):
        return False, "grupo (desativado nos filtros)"
    # em grupos, quem fala é o participante; em 1:1, o próprio jid
    sender = _norm_br(_digits(m.get("sender") or m.get("jid") or ""))

    def _match(entries: Any) -> bool:
        for e in entries or []:
            d = _norm_br(_digits(str(e)))
            if d and (sender.endswith(d) or d.endswith(sender)):
                return True
        return False

    policy = f.get("policy") or "all"
    if policy == "allow" and not _match(f.get("allow")):
        return False, "fora da lista de permissão"
    if policy == "block" and _match(f.get("block")):
        return False, "na lista de bloqueio"
    trigger = (f.get("trigger") or "").strip()
    if trigger and not m.get("text", "").lower().startswith(trigger.lower()):
        return False, "sem o prefixo-gatilho"
    return True, ""


async def _resolve_thread(
    db, conn: WhatsAppConnection, user: User, m: dict[str, Any], mc: ModelConfig | None
) -> tuple[WhatsAppThread, Chat]:
    """Conversa (jid) ↔ Chat do app: acha ou cria o par."""
    thread = await db.scalar(
        select(WhatsAppThread).where(
            WhatsAppThread.connection_id == conn.id, WhatsAppThread.jid == m["jid"]
        )
    )
    if thread is not None:
        chat = await db.get(Chat, thread.chat_id)
        if chat is not None:
            if m.get("sender_name") and thread.contact_name != m["sender_name"]:
                thread.contact_name = m["sender_name"]
            return thread, chat
        await db.delete(thread)  # chat apagado pelo usuário → recria o par
        await db.flush()

    who = m.get("sender_name") or _digits(m["jid"]) or "contato"
    chat = Chat(
        user_id=user.id,
        title=f"WhatsApp · {who}"[:255],
        model=conn.model or (mc.base_model if mc else ""),
        model_config_id=mc.id if mc else None,
        params=(mc.params if mc else {}) or {},
    )
    db.add(chat)
    await db.flush()
    thread = WhatsAppThread(
        connection_id=conn.id, jid=m["jid"], chat_id=chat.id,
        contact_name=m.get("sender_name") or "",
    )
    db.add(thread)
    await db.flush()
    return thread, chat


def _memory_kwargs(conn: WhatsAppConnection, chat: Chat, mc: ModelConfig | None, model: str) -> dict:
    """Política de memória da conexão:
    - "local": memórias isoladas por conversa do WhatsApp (escopo chat);
    - "global": lê e alimenta a memória compartilhada do modelo (junto com os
      outros canais)."""
    from ..chat.routes import _mem_agent_id  # import tardio (evita ciclo)

    agent_id = _mem_agent_id(mc, model)
    # bancos acoplados ao modelo: compartilhados também nas conversas do WhatsApp
    banks = [str(b) for b in ((mc.capabilities or {}).get("memory") or {}).get("banks", [])] if mc else []
    if conn.memory == "global":
        return {
            "chat_id": str(chat.id), "agent_id": agent_id, "mem_banks": banks,
            "mem_read": {"global": True, "model": True, "chat": True},
            "mem_write": "model",
        }
    return {
        "chat_id": str(chat.id), "agent_id": agent_id, "mem_banks": banks,
        "mem_read": {"global": False, "model": False, "chat": True},
        "mem_write": "chat",
    }


async def _send_reply(conn: WhatsAppConnection, jid: str, text: str) -> None:
    if conn.provider == "official":
        await official.send_text(conn.phone_number_id, conn.access_token, jid, text)
    else:
        await evolution.send_text(conn.instance, jid, text)


async def _run_one(connection_id: uuid.UUID, m: dict[str, Any]) -> None:
    """Um turno completo para UMA mensagem aprovada (sessão própria)."""
    from ..chat.orchestrator import run_turn_guarded
    from ..chat.routes import (
        _load_skills, _resolve_guards, _resolve_provider, _usage_record, _user_profile_dict,
    )
    from ..tools.loader import get_sift_for_user

    async with SessionLocal() as db:
        conn = await db.get(WhatsAppConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        user = await db.get(User, conn.user_id)
        if user is None:
            return

        mc = None
        if conn.model_config_id:
            mc = await db.get(ModelConfig, conn.model_config_id)
            if mc is not None and mc.user_id != user.id:
                mc = None
        model = conn.model or (mc.base_model if mc else "")
        if not model:
            conn.state = {**(conn.state or {}), "last_error": "Conexão sem modelo definido"}
            await db.commit()
            return

        try:
            api_key, base_url = await _resolve_provider(db, user, model)
        except Exception as exc:  # noqa: BLE001 - HTTPException fora de request
            conn.state = {**(conn.state or {}), "last_error": getattr(exc, "detail", None) or _err_text(exc)}
            await db.commit()
            return

        thread, chat = await _resolve_thread(db, conn, user, m, mc)

        # prefixo-gatilho configurado → o modelo recebe o texto sem o prefixo
        text = m["text"]
        trigger = ((conn.filters or {}).get("trigger") or "").strip()
        if trigger and text.lower().startswith(trigger.lower()):
            text = text[len(trigger):].strip() or text

        # histórico = o próprio chat da conversa (limitado)
        rows = list(await db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc())
            .limit(40)
        ))
        history = [{"role": r.role, "content": r.content} for r in reversed(rows) if r.content]

        # transcrição legível: em grupo, marca quem falou
        shown = f"{m['sender_name']}: {text}" if m.get("is_group") and m.get("sender_name") else text
        db.add(Message(chat_id=chat.id, role="user", content=shown))

        sift = await get_sift_for_user(db, user.id, mc)
        skills = await _load_skills(db, user, mc)
        # filtros do modelo que fazem sentido em texto: Guardas de saída (os de
        # imagem — vision/genimage router — não se aplicam a mensagens do WhatsApp)
        guards = await _resolve_guards(db, user, mc)
        who = m.get("sender_name") or _digits(m["jid"])
        extra_system = (
            f"You are replying on WhatsApp (connection '{conn.label or conn.phone}') to "
            f"{who}. Answer as a WhatsApp message: concise, plain text (WhatsApp only "
            f"renders *bold*, _italic_ and ```code```; never use headings, tables or links "
            f"in markdown syntax). Match the contact's language."
        )

        content = ""
        usage = None
        reasoning = None
        tool_events = None
        error = None
        try:
            async for ev in run_turn_guarded(
                guards=guards,
                api_key=api_key, model=model, history=history, user_text=text,
                chat_system_prompt=mc.system_prompt if mc else None,
                params=(mc.params if mc else {}) or {},
                user_id=str(user.id), base_url=base_url,
                background=True,  # autônomo: sem revisão interativa de tools
                sift=sift, code_mode=bool(getattr(mc, "code_mode", False)),
                skills=skills, use_context=True, extra_system=extra_system,
                user_profile=_user_profile_dict(user),
                **_memory_kwargs(conn, chat, mc, model),
            ):
                if ev["type"] == "done":
                    content = ev.get("content", "")
                    usage = ev.get("usage")
                    reasoning = ev.get("reasoning")
                    tool_events = ev.get("tool_events")
                elif ev["type"] == "error":
                    error = ev.get("message") or "Falha no modelo"
        except Exception as exc:  # noqa: BLE001
            error = _err_text(exc)

        if error or not content:
            conn.state = {**(conn.state or {}), "last_error": error or "Resposta vazia"}
            await db.commit()
            logger.warning("whatsapp: turno falhou (%s): %s", conn.id, error)
            return

        rec = _usage_record(usage, model, mc)
        msg = Message(
            chat_id=chat.id, role="assistant", content=content,
            tokens=rec["total_tokens"] or None, cost=rec["cost"] or None,
            usage=rec, reasoning=reasoning, tool_events=tool_events,
        )
        db.add(msg)
        await db.flush()
        ev_row = usage_event_from_record(user.id, chat.id, msg.id, rec)
        if ev_row is not None:
            db.add(ev_row)
        thread.last_message_at = datetime.now(timezone.utc)

        try:
            await _send_reply(conn, m["jid"], content)
            conn.state = {**(conn.state or {}), "last_error": None,
                          "last_event_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:  # noqa: BLE001 - resposta gerada mas não entregue
            conn.state = {**(conn.state or {}), "last_error": f"Falha ao enviar: {_err_text(exc)}"}
            logger.warning("whatsapp: envio falhou (%s): %s", conn.id, exc)
        await db.commit()


async def handle_incoming(connection_id: uuid.UUID, messages: list[dict[str, Any]]) -> None:
    """Processa as mensagens de um webhook (já normalizadas). Roda como task de
    fundo — o webhook responde 200 imediatamente."""
    async with SessionLocal() as db:
        conn = await db.get(WhatsAppConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        approved = []
        for m in messages:
            ok, reason = passes_filters(conn, m)
            if ok:
                approved.append(m)
            else:
                logger.info("whatsapp: mensagem filtrada (%s): %s", conn.id, reason)
    for m in approved:
        async with _lock(f"{connection_id}|{m['jid']}"):
            try:
                await _run_one(connection_id, m)
            except Exception:  # noqa: BLE001 - nunca derruba o loop de webhooks
                logger.exception("whatsapp: falha ao processar mensagem (%s)", connection_id)
