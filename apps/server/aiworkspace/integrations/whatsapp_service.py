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
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from ..db import SessionLocal
from ..models import (
    Chat,
    Folder,
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


# Deduplicação de mensagens já processadas (por msg_id), limitada em memória.
# O Evolution/Baileys REENVIA o histórico recente como MESSAGES_UPSERT quando a
# sessão reconecta (e a Meta pode retentar webhooks). Sem isto, a IA respondia
# tudo de novo — re-cumprimentava e re-respondia mensagens antigas.
_seen_ids: set[str] = set()
_seen_order: deque[str] = deque(maxlen=2000)

# Mensagens mais antigas que isto (pelo messageTimestamp do WhatsApp) são
# descartadas: são histórico reenviado na reconexão, não conversa nova. Folga
# grande p/ não perder mensagens legítimas atrasadas por uma resposta longa.
_MAX_AGE_SECONDS = 600


def _seen(msg_id: str) -> bool:
    """True se este msg_id já foi processado (e registra os novos)."""
    if not msg_id:
        return False  # sem id não dá p/ deduplicar — deixa passar
    if msg_id in _seen_ids:
        return True
    _seen_order.append(msg_id)  # deque limitada evita o append crescer sem fim
    _seen_ids.add(msg_id)
    # poda em lote (rara): ressincroniza o set com a janela recente da deque
    if len(_seen_ids) > 2 * (_seen_order.maxlen or 2000):
        _seen_ids.intersection_update(_seen_order)
    return False


def _too_old(m: dict[str, Any]) -> bool:
    ts = int(m.get("ts") or 0)
    return ts > 0 and (time.time() - ts) > _MAX_AGE_SECONDS


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


async def _find_or_create_folder(db, user_id, name: str, parent_id) -> Folder:
    folder = await db.scalar(
        select(Folder).where(
            Folder.user_id == user_id, Folder.name == name, Folder.parent_id == parent_id
        )
    )
    if folder is None:
        folder = Folder(user_id=user_id, name=name, parent_id=parent_id)
        db.add(folder)
        await db.flush()
    return folder


async def _ensure_folder(db, conn: WhatsAppConnection, user: User):
    """Pasta das conversas desta conexão: WhatsApp/<número>/Chats. Criada sob
    demanda e memorizada em conn.folder_id (renomear/mover pastas é respeitado —
    só recriamos se a pasta for excluída)."""
    if conn.folder_id is not None:
        folder = await db.get(Folder, conn.folder_id)
        if folder is not None:
            return folder.id
        conn.folder_id = None  # pasta excluída pelo usuário → recria a estrutura
    try:
        root = await _find_or_create_folder(db, user.id, "WhatsApp", None)
        label = (f"+{conn.phone}" if conn.phone else "") or conn.label or "Número"
        number = await _find_or_create_folder(db, user.id, label[:255], root.id)
        chats = await _find_or_create_folder(db, user.id, "Chats", number.id)
    except Exception:  # noqa: BLE001 - organização nunca derruba o turno
        logger.exception("whatsapp: falha ao criar pastas (%s)", conn.id)
        return None
    conn.folder_id = chats.id
    return chats.id


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
            if chat.folder_id is None:  # adota chats antigos/desapastados
                chat.folder_id = await _ensure_folder(db, conn, user)
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
        folder_id=await _ensure_folder(db, conn, user),
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


# janelas dos limites de uso ("total" = permanente, sem janela)
_LIMIT_WINDOWS: list[tuple[str, timedelta | None, str]] = [
    ("total", None, "permanente"),
    ("per_hour", timedelta(hours=1), "por hora"),
    ("per_day", timedelta(days=1), "por dia"),
    ("per_month", timedelta(days=30), "por mês"),
]


async def _limit_reached(db, conn: WhatsAppConnection, chat: Chat) -> str | None:
    """Limites de uso por número que contata: conta as mensagens JÁ processadas
    desse contato (mensagens "user" do chat da conversa) contra cada janela.
    Retorna o rótulo do limite estourado, ou None. Janelas são deslizantes
    (renovam sozinhas); "total" só zera se o dono apagar o chat."""
    lm = conn.limits or {}
    now = datetime.now(timezone.utc)
    for key, window, label in _LIMIT_WINDOWS:
        try:
            cap = int(lm.get(key) or 0)
        except (TypeError, ValueError):
            cap = 0
        if cap <= 0:
            continue
        q = (
            select(func.count())
            .select_from(Message)
            .where(Message.chat_id == chat.id, Message.role == "user")
        )
        if window is not None:
            q = q.where(Message.created_at >= now - window)
        n = await db.scalar(q) or 0
        if n >= cap:
            return f"{label} ({n}/{cap})"
    return None


def _contact_note(conn: WhatsAppConnection, m: dict[str, Any]) -> str:
    """Contexto/role configurado para o número que está falando (se houver)."""
    sender = _norm_br(_digits(m.get("sender") or m.get("jid") or ""))
    if not sender:
        return ""
    for c in conn.contacts or []:
        d = _norm_br(_digits(str(c.get("number") or "")))
        if not d or not (sender.endswith(d) or d.endswith(sender)):
            continue
        who = " — ".join(p for p in [str(c.get("name") or "").strip(), str(c.get("role") or "").strip()] if p)
        ctx = str(c.get("context") or "").strip()
        note = f"About this contact{f' ({who})' if who else ''}"
        return f"{note}: {ctx}" if ctx else (note if who else "")
    return ""


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


# --------------------------------------------------------------------------- #
# Modo humanizador: "digitando…", atraso proporcional e quebra em várias
# mensagens — em vez de despejar um bloco só de texto instantâneo.
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ý0-9\"'(])")


def _humanize_cfg(conn: WhatsAppConnection) -> dict[str, Any]:
    h = conn.humanize or {}
    return {
        "enabled": bool(h.get("enabled")),
        "typing": h.get("typing", True) is not False,
        "split": bool(h.get("split")),
        "min_seconds": max(0, int(h.get("min_seconds", 1) or 0)),
        "max_seconds": max(0, int(h.get("max_seconds", 6) or 0)),
    }


def _split_message(text: str, max_parts: int = 5) -> list[str]:
    """Quebra um texto em mensagens naturais: por parágrafos (linha em branco);
    se veio um bloco só, tenta por frases. Junta o excedente na última parte."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) == 1:
        sents = [s.strip() for s in _SENTENCE_SPLIT.split(parts[0]) if s.strip()]
        if len(sents) > 1:
            parts = sents
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + [" ".join(parts[max_parts - 1:])]
    return parts


def _delay_seconds(cfg: dict, chunk: str) -> float:
    """Tempo 'humano' antes de enviar um trecho: ~cadência de digitação, preso
    entre min e max da configuração."""
    lo = float(cfg["min_seconds"])
    hi = float(cfg["max_seconds"]) if cfg["max_seconds"] else max(lo, 6.0)
    est = len(chunk) / 22.0  # ~22 caracteres por segundo
    return max(lo, min(est, hi))


async def _hold_typing(conn: WhatsAppConnection, jid: str, seconds: float) -> None:
    """Dorme `seconds` mantendo 'digitando…' vivo (renova a presença a cada ~4s).
    Só a Evolution expõe presença; no oficial isto vira só o atraso."""
    deadline = time.monotonic() + seconds
    if conn.provider != "evolution":
        await asyncio.sleep(min(seconds, 30))
        return
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        step = min(4.0, remaining)
        await evolution.send_presence(conn.instance, jid, "composing", int((step + 0.5) * 1000))
        await asyncio.sleep(step)


async def _deliver(conn: WhatsAppConnection, jid: str, text: str) -> None:
    """Entrega a resposta ao contato. Com o Modo humanizador ligado, mostra
    'digitando…', espera um tempo proporcional e (opcional) manda em partes."""
    cfg = _humanize_cfg(conn)
    if not cfg["enabled"]:
        await _send_reply(conn, jid, text)
        return
    chunks = _split_message(text) if cfg["split"] else [(text or "").strip()]
    for chunk in chunks:
        if not chunk:
            continue
        delay = _delay_seconds(cfg, chunk)
        if cfg["typing"]:
            await _hold_typing(conn, jid, delay)  # dorme "digitando"
        elif delay > 0:
            await asyncio.sleep(min(delay, 30))
        await _send_reply(conn, jid, chunk)


async def send_outbound(
    db, conn: WhatsAppConnection, user: User, jid: str, text: str, contact_name: str = ""
) -> None:
    """Envio PROATIVO (ex.: disparado por automação): registra a mensagem no chat
    da conversa (para aparecer na sidebar/histórico) e entrega com humanizador."""
    mc = None
    if conn.model_config_id:
        mc = await db.get(ModelConfig, conn.model_config_id)
        if mc is not None and mc.user_id != user.id:
            mc = None
    thread, chat = await _resolve_thread(
        db, conn, user, {"jid": jid, "sender_name": contact_name}, mc
    )
    db.add(Message(chat_id=chat.id, role="assistant", content=text))
    thread.last_message_at = datetime.now(timezone.utc)
    await db.commit()
    await _deliver(conn, jid, text)


def _to_jid(number: str) -> str:
    """Número em dígitos → jid canônico de contato (evita thread duplicada com o
    formato que chega no inbound)."""
    d = _digits(number)
    return f"{d}@s.whatsapp.net" if d else ""


_MAX_RECIPIENTS = 100  # teto de segurança p/ um disparo (anti-spam/ban)


async def resolve_recipients(db, conn: WhatsAppConnection, wa: dict) -> list[dict[str, str]]:
    """Destinatários de um envio proativo, conforme o modo escolhido:
    - "number":   um número específico (wa["number"]);
    - "contacts": os contatos cadastrados na conexão (lista de roles);
    - "threads":  todas as conversas existentes desta conexão (quem já falou)."""
    mode = (wa or {}).get("to") or "number"
    out: list[dict[str, str]] = []
    if mode == "number":
        jid = _to_jid(str(wa.get("number") or ""))
        if jid:
            out.append({"jid": jid, "name": ""})
    elif mode == "contacts":
        for c in conn.contacts or []:
            jid = _to_jid(str(c.get("number") or ""))
            if jid:
                out.append({"jid": jid, "name": str(c.get("name") or "")})
    elif mode == "threads":
        rows = list(await db.scalars(
            select(WhatsAppThread).where(WhatsAppThread.connection_id == conn.id)
        ))
        for t in rows:
            if t.jid:
                out.append({"jid": t.jid, "name": t.contact_name or ""})
    # dedup por jid, preservando ordem, com teto de segurança
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for r in out:
        if r["jid"] in seen:
            continue
        seen.add(r["jid"])
        uniq.append(r)
        if len(uniq) >= _MAX_RECIPIENTS:
            break
    return uniq


async def broadcast(connection_id: uuid.UUID, recipients: list[dict[str, str]], text: str) -> None:
    """Entrega `text` a vários destinatários por uma conexão (sessão própria).
    `recipients` = [{jid, name}]. Um lock por conversa evita colidir com respostas
    de inbound em andamento. Cada destinatário é isolado (falha de um não derruba
    os demais)."""
    text = (text or "").strip()
    if not text or not recipients:
        return
    async with SessionLocal() as db:
        conn = await db.get(WhatsAppConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        user = await db.get(User, conn.user_id)
        if user is None:
            return
        for r in recipients:
            jid = r.get("jid") or ""
            if not jid:
                continue
            async with _lock(f"{connection_id}|{jid}"):
                try:
                    await send_outbound(db, conn, user, jid, text, r.get("name") or "")
                except Exception:  # noqa: BLE001 - um destinatário não derruba o resto
                    logger.exception("whatsapp: falha ao enviar proativo p/ %s (%s)", jid, conn.id)


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

        # limites de uso deste contato: estourou → a mensagem não é processada
        # (a janela renova sozinha; "permanente" só zera apagando o chat)
        hit = await _limit_reached(db, conn, chat)
        if hit is not None:
            await db.commit()  # persiste pastas/threads criadas na resolução
            logger.info("whatsapp: limite %s atingido (%s, %s) — mensagem ignorada",
                        hit, conn.id, m["jid"])
            return

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
        extra_parts = [
            f"You are replying on WhatsApp (connection '{conn.label or conn.phone}') to "
            f"{who}. Answer as a WhatsApp message: concise, plain text (WhatsApp only "
            f"renders *bold*, _italic_ and ```code```; never use headings, tables or links "
            f"in markdown syntax). Match the contact's language."
        ]
        # prompt adicional configurado para ESTE número conectado
        if (conn.system_prompt or "").strip():
            extra_parts.append(conn.system_prompt.strip())
        # contexto/role do contato que está falando (lista da conexão)
        note = _contact_note(conn, m)
        if note:
            extra_parts.append(note)
        extra_system = "\n\n".join(extra_parts)

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
            await _deliver(conn, m["jid"], content)
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
            # 1) histórico reenviado na reconexão (timestamp antigo) → ignora
            if _too_old(m):
                logger.info("whatsapp: mensagem antiga ignorada (%s): id=%s", conn.id, m.get("msg_id"))
                continue
            # 2) já processada (reentrega de webhook / retry) → ignora
            if _seen(m.get("msg_id") or ""):
                logger.info("whatsapp: duplicata ignorada (%s): id=%s", conn.id, m.get("msg_id"))
                continue
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
