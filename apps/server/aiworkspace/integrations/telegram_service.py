"""Serviço da integração Telegram: recebe updates, roda o modelo e responde.

Espelha o `whatsapp_service` (inbound → run_turn_guarded → reply), mas via Bot API
com long-polling. O poller (`telegram_poller`) chama `handle_update` por update.
Também expõe entrega para automações (`resolve_recipients`/`broadcast`).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from . import channel_folders, channel_media, inbound_batch
from ..models import (
    Chat, Folder, Message, ModelConfig, TelegramConnection, TelegramThread, User,
)
from ..usage_service import usage_event_from_record
from . import telegram_api

logger = logging.getLogger(__name__)

_MAX_RECIPIENTS = 200


def _err_text(exc: BaseException) -> str:
    return getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__


# --------------------------------------------------------------------------- #
# Extração/filtragem do update
# --------------------------------------------------------------------------- #
def parse_update(u: dict[str, Any]) -> dict[str, Any] | None:
    """Extrai o essencial de um update: mensagem de TEXTO ou de VOZ/ÁUDIO
    (o Audio Router do modelo transcreve). Ignora o resto."""
    msg = u.get("message") or {}
    text = msg.get("text")
    voice = msg.get("voice") or msg.get("audio") or {}
    chat = msg.get("chat") or {}
    frm = msg.get("from") or {}
    if (not text and not voice.get("file_id")) or not chat.get("id"):
        return None
    ctype = chat.get("type") or "private"
    name = " ".join(p for p in [frm.get("first_name"), frm.get("last_name")] if p) or frm.get("username") or "contato"
    return {
        "update_id": u.get("update_id"),
        "tg_chat_id": str(chat["id"]),
        "text": text or "",
        "audio_file_id": voice.get("file_id") or "",
        "audio_mime": voice.get("mime_type") or "audio/ogg",
        "sender_name": name,
        "sender_id": str(frm.get("id") or ""),
        "username": (frm.get("username") or "").lower(),
        "is_group": ctype in ("group", "supergroup"),
        "msg_id": str(msg.get("message_id") or ""),
    }


def passes_filters(conn: TelegramConnection, m: dict[str, Any]) -> tuple[bool, str]:
    f = conn.filters or {}
    if m["is_group"] and not f.get("groups", False):
        return False, "grupos desativados"
    ids = {str(x).lower().lstrip("@") for x in (f.get("block") or [])}
    if ids and (m["sender_id"] in ids or (m["username"] and m["username"] in ids)):
        return False, "remetente bloqueado"
    allow = {str(x).lower().lstrip("@") for x in (f.get("allow") or [])}
    if allow and not (m["sender_id"] in allow or (m["username"] and m["username"] in allow)):
        return False, "fora da lista de permitidos"
    trigger = (f.get("trigger") or "").strip()
    if trigger and not m["text"].lower().startswith(trigger.lower()):
        return False, "sem prefixo-gatilho"
    return True, ""


# --------------------------------------------------------------------------- #
# Pasta + thread ↔ chat
# --------------------------------------------------------------------------- #
async def _find_or_create_folder(db, user_id, name: str, parent_id) -> Folder:
    folder = await db.scalar(
        select(Folder).where(
            Folder.user_id == user_id, Folder.name == name,
            Folder.parent_id == parent_id,
        )
    )
    if folder is None:
        folder = Folder(user_id=user_id, name=name, parent_id=parent_id)
        db.add(folder)
        await db.flush()
    return folder


async def _ensure_folder(db, conn: TelegramConnection, user: User):
    # os chats ficam DIRETO na pasta do bot (a subpasta "Chats" era um nivel a
    # mais sem funcao; o layout antigo e colapsado ao ser encontrado)
    if conn.folder_id:
        f = await db.get(Folder, conn.folder_id)
        if f is not None:
            migrated = await channel_folders.collapse_chats_folder(db, f)
            if migrated is not None:
                conn.folder_id = migrated
                return migrated
            return f.id
    root = await _find_or_create_folder(db, user.id, "Telegram", None)
    who = conn.bot_username or conn.label or "bot"
    sub = await _find_or_create_folder(db, user.id, f"@{who}", root.id)
    conn.folder_id = sub.id
    return sub.id


async def _resolve_thread(
    db, conn: TelegramConnection, user: User, m: dict[str, Any], mc: ModelConfig | None
) -> tuple[TelegramThread, Chat]:
    thread = await db.scalar(
        select(TelegramThread).where(
            TelegramThread.connection_id == conn.id, TelegramThread.tg_chat_id == m["tg_chat_id"]
        )
    )
    if thread is not None:
        chat = await db.get(Chat, thread.chat_id)
        if chat is not None:
            if m.get("sender_name") and thread.contact_name != m["sender_name"]:
                thread.contact_name = m["sender_name"]
            if chat.folder_id is None:
                chat.folder_id = await _ensure_folder(db, conn, user)
            return thread, chat
        await db.delete(thread)
        await db.flush()

    chat = Chat(
        user_id=user.id,
        title=f"Telegram · {m.get('sender_name') or m['tg_chat_id']}"[:255],
        model=(mc.base_model if mc else "") or conn.model,
        model_config_id=mc.id if mc else None,
        params=(mc.params if mc else {}) or {},
        folder_id=await _ensure_folder(db, conn, user),
    )
    db.add(chat)
    await db.flush()
    thread = TelegramThread(
        connection_id=conn.id, tg_chat_id=m["tg_chat_id"], chat_id=chat.id,
        contact_name=m.get("sender_name") or "", is_group=m.get("is_group", False),
    )
    db.add(thread)
    await db.flush()
    return thread, chat


def _memory_setup(conn: TelegramConnection, chat: Chat, mc: ModelConfig | None, model: str):
    """Política de memória da conexão → (chat_id, agent_id, MemoryOpts) — ver WhatsApp."""
    from ..chat.orchestrator import MemoryOpts
    from ..chat.turn_setup import _mem_agent_id

    agent_id = _mem_agent_id(mc, model)
    banks = [str(b) for b in ((mc.capabilities or {}).get("memory") or {}).get("banks", [])] if mc else []
    if conn.memory == "global":
        mem = MemoryOpts(read={"global": True, "model": True, "chat": True}, write="model", banks=banks)
    else:
        mem = MemoryOpts(read={"global": False, "model": False, "chat": True}, write="chat", banks=banks)
    return str(chat.id), agent_id, mem


# --------------------------------------------------------------------------- #
# Entrega (humanizador: digitando… + atraso + quebra)
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ý0-9\"'(])")


def _humanize_cfg(conn: TelegramConnection) -> dict[str, Any]:
    h = conn.humanize or {}
    return {
        "enabled": bool(h.get("enabled")),
        "typing": h.get("typing", True),
        "min_seconds": float(h.get("min_seconds", 1.0) or 0),
        "max_seconds": float(h.get("max_seconds", 4.0) or 0),
        "split": bool(h.get("split")),
    }


def _split_message(text: str, max_parts: int = 5) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n{2,}", text.strip()) if p.strip()]
    parts = paras if len(paras) > 1 else [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    if len(parts) <= 1:
        return [text.strip()]
    if len(parts) > max_parts:
        head, tail = parts[: max_parts - 1], parts[max_parts - 1:]
        parts = head + ["\n\n".join(tail)]
    return parts


def _delay_seconds(cfg: dict, chunk: str) -> float:
    lo, hi = cfg["min_seconds"], max(cfg["max_seconds"], cfg["min_seconds"])
    base = random.uniform(lo, hi) if hi > 0 else 0.0
    return min(base + len(chunk) / 900.0, 12.0)  # ~ tempo de digitar


async def _deliver(conn: TelegramConnection, token: str, tg_chat_id: str, text: str) -> None:
    cfg = _humanize_cfg(conn)
    chunks = _split_message(text) if cfg["enabled"] and cfg["split"] else [text]
    for i, chunk in enumerate(chunks):
        if cfg["enabled"]:
            if cfg["typing"]:
                await telegram_api.send_chat_action(token, tg_chat_id, "typing")
            wait = _delay_seconds(cfg, chunk)
            if wait > 0:
                await asyncio.sleep(wait)
        await telegram_api.send_message(token, tg_chat_id, chunk)


# --------------------------------------------------------------------------- #
# Turno de modelo por mensagem recebida
# --------------------------------------------------------------------------- #
async def _run_one(connection_id: uuid.UUID, msgs: list[dict[str, Any]]) -> None:
    """Um turno para o LOTE de mensagens (ver inbound_batch). Sem janela de agregação
    o lote tem uma mensagem só e o comportamento é o de antes."""
    m = msgs[-1]  # identidade da conversa = a última do lote
    from ..chat.orchestrator import MediaOpts, TurnSession, run_turn_guarded
    from ..chat.turn_setup import (
        _audio_router_config, _code_mode, _genimage_config, _load_skills, _profile_tz,
        _resolve_guards, _resolve_provider, _usage_record, _user_profile_dict,
    )
    from ..tools.loader import get_sift_for_user

    async with SessionLocal() as db:
        conn = await db.get(TelegramConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        user = await db.get(User, conn.user_id)
        if user is None:
            return
        token = conn.bot_token
        mc = None
        if conn.model_config_id:
            mc = await db.get(ModelConfig, conn.model_config_id)
            if mc is not None and mc.user_id != user.id:
                mc = None
        # com ModelConfig acoplado, o modelo DELE manda (conn.model = snapshot
        # do painel, envelhece quando o agente troca de modelo)
        model = (mc.base_model if mc else "") or conn.model
        if not model:
            conn.state = {**(conn.state or {}), "last_error": "Conexão sem modelo definido"}
            await db.commit()
            return

        from ..budget_service import budget_state
        if (await budget_state(db, user)).get("blocked"):
            conn.state = {**(conn.state or {}), "last_error": "Orçamento mensal atingido (pausado)"}
            await db.commit()
            return

        try:
            api_key, base_url = await _resolve_provider(db, user, model)
        except Exception as exc:  # noqa: BLE001
            conn.state = {**(conn.state or {}), "last_error": _err_text(exc)}
            await db.commit()
            return

        thread, chat = await _resolve_thread(db, conn, user, m, mc)

        # o lote (janela de agregação) vira um texto só — o contato fragmentou a fala
        trigger = ((conn.filters or {}).get("trigger") or "").strip()

        def _strip_trigger(t: str) -> str:
            if trigger and t.lower().startswith(trigger.lower()):
                return t[len(trigger):].strip() or t
            return t

        text = "\n".join(_strip_trigger(x["text"]) for x in msgs if (x.get("text") or "").strip())

        # Notas de voz → baixa via getFile e deixa o Audio Router transcrever
        attachments: list[dict[str, Any]] = []
        audio_router = None
        audio_msgs = [x for x in msgs if x.get("audio_file_id")]
        if audio_msgs:
            audio_router = await _audio_router_config(db, user, mc)
            for i, x in enumerate(audio_msgs, 1):
                try:
                    raw = await telegram_api.get_file_bytes(token, x["audio_file_id"])
                    b64 = base64.b64encode(raw).decode()
                    mime = x.get("audio_mime") or "audio/ogg"
                    attachments.append({
                        "type": "audio", "name": f"voz{i}.ogg", "mime": mime,
                        "url": f"data:{mime};base64,{b64}",
                    })
                except Exception as exc:  # noqa: BLE001 - áudio indisponível não trava o turno
                    logger.warning("telegram: falha ao baixar áudio (%s): %s", conn.id, exc)

        rows = list(await db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc()).limit(channel_media.history_limit(conn))
        ))
        history = [{"role": r.role, "content": r.content} for r in reversed(rows) if r.content]
        display = text or ("[Mensagem de voz]" if attachments else text)
        shown = f"{m['sender_name']}: {display}" if m.get("is_group") and m.get("sender_name") else display
        db.add(Message(chat_id=chat.id, role="user", content=shown))

        # sem as tools que o canal não entrega (diagrama); gráfico e imagem VÃO como mídia
        sift = await get_sift_for_user(db, user.id, channel_media.sift_view(mc))
        skills = await _load_skills(db, user, mc)
        guards = await _resolve_guards(db, user, mc)
        genimage = await _genimage_config(db, user, mc)
        extra_parts = [
            f"You are replying on Telegram (bot '@{conn.bot_username or conn.label}') to "
            f"{m.get('sender_name')}. Answer as a Telegram message: concise, plain text; "
            f"avoid markdown headings/tables. Charts and generated images ARE delivered to "
            f"the contact as real media, so you may use them. Match the contact's language."
        ]
        if (conn.system_prompt or "").strip():
            extra_parts.append(conn.system_prompt.strip())
        extra_system = "\n\n".join(extra_parts)

        # COMMIT antes do turno (depois das queries de preparação): devolve a conexão
        # ao pool durante o run_turn e preserva a mensagem recebida se o processo
        # cair no meio (ver whatsapp_service).
        await db.commit()

        content = ""
        usage = reasoning = tool_events = None
        error = None
        try:
            mem_chat_id, mem_agent_id, mem_opts = _memory_setup(conn, chat, mc, model)
            async for ev in run_turn_guarded(
                guards=guards, api_key=api_key, model=model, history=history, user_text=text,
                chat_system_prompt=mc.system_prompt if mc else None,
                params=(mc.params if mc else {}) or {},
                base_url=base_url,
                session=TurnSession(
                    user_id=str(user.id), background=True,
                    chat_id=mem_chat_id, agent_id=mem_agent_id,
                    user_profile=_user_profile_dict(user),
                    user_tz=_profile_tz(user),  # sem isso, hora UTC vira "local"
                ),
                sift=sift, code_mode=_code_mode(mc),
                skills=skills, use_context=True, extra_system=extra_system,
                extra_breakdown={"channel": len(extra_system)},
                memory=mem_opts,
                media=MediaOpts(attachments=attachments or None, audio_router=audio_router,
                                genimage=genimage),
            ):
                if ev["type"] == "done":
                    content = ev.get("content", "")
                    usage = ev.get("usage"); reasoning = ev.get("reasoning"); tool_events = ev.get("tool_events")
                elif ev["type"] == "error":
                    error = ev.get("message") or "Falha no modelo"
        except Exception as exc:  # noqa: BLE001
            error = _err_text(exc)

        # grafico/imagem do turno -> midia de verdade (sendPhoto)
        media = await channel_media.collect(tool_events)
        # imagem da Base de Conhecimento colada como markdown na resposta -> midia
        # (no canal o link local seria inutil); o texto segue sem o markdown
        out_text, kb_media = await channel_media.extract_content_images(content)
        media.extend(kb_media)

        # resposta so com imagem e legitima ("me faz um grafico"): nao e "vazia"
        if error or not (content or media):
            conn.state = {**(conn.state or {}), "last_error": error or "Resposta vazia"}
            await db.commit()
            logger.warning("telegram: turno falhou (%s): %s", conn.id, error)
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
            if out_text:
                await _deliver(conn, token, m["tg_chat_id"], out_text)
            for item in media:
                await telegram_api.send_photo(
                    token, m["tg_chat_id"], item["data"], item["filename"], item["caption"],
                )
            conn.state = {**(conn.state or {}), "last_error": None,
                          "last_event_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:  # noqa: BLE001
            conn.state = {**(conn.state or {}), "last_error": f"Falha ao enviar: {_err_text(exc)}"}
            logger.warning("telegram: envio falhou (%s): %s", conn.id, exc)
        await db.commit()


async def handle_update(connection_id: uuid.UUID, update: dict[str, Any]) -> None:
    """Processa UM update: filtra e roda o turno (sessão própria em _run_one)."""
    m = parse_update(update)
    if m is None:
        return
    async with SessionLocal() as db:
        conn = await db.get(TelegramConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        ok, reason = passes_filters(conn, m)
        debounce = conn.debounce_seconds or 0  # lido DENTRO da sessão
    if not ok:
        logger.info("telegram: update filtrado (%s): %s", connection_id, reason)
        return
    try:
        # janela de agregação por remetente; lock por conversa (ver inbound_batch)
        await inbound_batch.submit(
            convo=f"{connection_id}|{m['tg_chat_id']}",
            sender=str(m.get("sender_name") or m["tg_chat_id"]),
            msg=m,
            seconds=debounce,
            runner=lambda batch: _run_one(connection_id, batch),
        )
    except Exception:  # noqa: BLE001
        logger.exception("telegram: falha ao processar update (%s)", connection_id)


# --------------------------------------------------------------------------- #
# Entrega para automações (broadcast)
# --------------------------------------------------------------------------- #
async def resolve_recipients(db, conn: TelegramConnection, tg: dict) -> list[str]:
    """Destinatários (tg_chat_id) conforme a config da automação:
      {mode: "chat"|"threads", chat_id?: "<id>"}. "threads" = todas as conversas
    conhecidas deste bot."""
    mode = (tg or {}).get("mode") or "threads"
    if mode == "chat" and (tg or {}).get("chat_id"):
        return [str(tg["chat_id"]).strip()]
    rows = list(await db.scalars(
        select(TelegramThread.tg_chat_id).where(TelegramThread.connection_id == conn.id)
    ))
    return [r for r in rows][:_MAX_RECIPIENTS]


async def broadcast(connection_id: uuid.UUID, recipients: list[str], text: str) -> None:
    if not recipients or not (text or "").strip():
        return
    async with SessionLocal() as db:
        conn = await db.get(TelegramConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        token, tg_chat_ids = conn.bot_token, list(recipients)
        cfg = _humanize_cfg(conn)
    for cid in tg_chat_ids:
        try:
            await _deliver_token(token, cid, text, cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram broadcast falhou p/ %s: %s", cid, exc)


async def _deliver_token(token: str, tg_chat_id: str, text: str, cfg: dict) -> None:
    chunks = _split_message(text) if cfg["enabled"] and cfg["split"] else [text]
    for chunk in chunks:
        if cfg["enabled"] and cfg["typing"]:
            await telegram_api.send_chat_action(token, tg_chat_id, "typing")
        if cfg["enabled"]:
            w = _delay_seconds(cfg, chunk)
            if w > 0:
                await asyncio.sleep(w)
        await telegram_api.send_message(token, tg_chat_id, chunk)
