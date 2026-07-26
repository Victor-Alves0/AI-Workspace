"""Serviço do CANAL Slack: recebe mensagens do Socket Mode, roda o modelo e responde.

Espelha o [[discord_service.py]] (inbound → run_turn_guarded → reply), mas o
recebimento vem de um evento `message` do Socket Mode. Cada canal/DM vira um Chat.
Entrega texto E mídia: gráficos/imagens gerados no turno viram anexos no Slack
(files.getUploadURLExternal → PUT → completeUploadExternal; requer `files:write`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Chat, Folder, Message, ModelConfig, SlackChannelConnection, SlackChannelThread, User
from ..usage_service import usage_event_from_record
from . import channel_folders, channel_media, inbound_batch, slack_channel_api

logger = logging.getLogger(__name__)


def _err_text(exc: BaseException) -> str:
    return getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__


# --------------------------------------------------------------------------- #
# Extração/filtragem do evento
# --------------------------------------------------------------------------- #
def parse_message(event: dict[str, Any], bot_user_id: str) -> dict[str, Any] | None:
    """Extrai o essencial de um evento `message`. Ignora mensagens de bots (inclui a
    si mesmo), subtipos (edições/entradas) e sem texto."""
    if event.get("bot_id") or event.get("subtype"):
        return None
    user_id = str(event.get("user") or "")
    if not user_id or (bot_user_id and user_id == bot_user_id):
        return None
    text = (event.get("text") or "").strip()
    channel_id = event.get("channel")
    if not text or not channel_id:
        return None
    ctype = event.get("channel_type") or ""
    # DMs têm channel_type "im"; como fallback (nem todo payload traz channel_type),
    # os ids de DM do Slack começam com "D".
    is_dm = ctype == "im" or str(channel_id).startswith("D")
    return {
        "channel_id": str(channel_id),
        "text": text,
        "sender_id": user_id,
        "is_dm": is_dm,
        "mentions_bot": bool(bot_user_id and f"<@{bot_user_id}>" in text),
        "ts": str(event.get("ts") or ""),
    }


def passes_filters(conn: SlackChannelConnection, m: dict[str, Any]) -> tuple[bool, str]:
    f = conn.filters or {}
    if not m["is_dm"] and not f.get("channels", True):
        return False, "canais desativados"
    if not m["is_dm"] and f.get("mention_only", True) and not m["mentions_bot"]:
        return False, "sem menção ao bot"
    ids = {str(x).lower().lstrip("@") for x in (f.get("block") or [])}
    if ids and m["sender_id"].lower() in ids:
        return False, "remetente bloqueado"
    allow = {str(x).lower().lstrip("@") for x in (f.get("allow") or [])}
    if allow and m["sender_id"].lower() not in allow:
        return False, "fora da lista de permitidos"
    trigger = (f.get("trigger") or "").strip()
    if trigger and not m["text"].lower().startswith(trigger.lower()):
        return False, "sem prefixo-gatilho"
    return True, ""


def _strip_mention(text: str, bot_user_id: str) -> str:
    if not bot_user_id:
        return text
    return re.sub(rf"<@{re.escape(bot_user_id)}>", "", text).strip() or text


# --------------------------------------------------------------------------- #
# Pasta + thread ↔ chat
# --------------------------------------------------------------------------- #
async def _find_or_create_folder(db, user_id, name: str, parent_id) -> Folder:
    folder = await db.scalar(
        select(Folder).where(
            Folder.user_id == user_id, Folder.name == name, Folder.parent_id == parent_id,
        )
    )
    if folder is None:
        folder = Folder(user_id=user_id, name=name, parent_id=parent_id)
        db.add(folder)
        await db.flush()
    return folder


async def _ensure_folder(db, conn: SlackChannelConnection, user: User):
    if conn.folder_id:
        f = await db.get(Folder, conn.folder_id)
        if f is not None:
            migrated = await channel_folders.collapse_chats_folder(db, f)
            if migrated is not None:
                conn.folder_id = migrated
                return migrated
            return f.id
    root = await _find_or_create_folder(db, user.id, "Slack", None)
    who = conn.team or conn.label or "workspace"
    sub = await _find_or_create_folder(db, user.id, f"#{who}", root.id)
    conn.folder_id = sub.id
    return sub.id


async def _resolve_thread(
    db, conn: SlackChannelConnection, user: User, m: dict[str, Any], contact: str, mc: ModelConfig | None
) -> tuple[SlackChannelThread, Chat]:
    thread = await db.scalar(
        select(SlackChannelThread).where(
            SlackChannelThread.connection_id == conn.id,
            SlackChannelThread.channel_id == m["channel_id"],
        )
    )
    if thread is not None:
        chat = await db.get(Chat, thread.chat_id)
        if chat is not None:
            if contact and thread.contact_name != contact:
                thread.contact_name = contact
            if chat.folder_id is None:
                chat.folder_id = await _ensure_folder(db, conn, user)
            return thread, chat
        await db.delete(thread)
        await db.flush()

    chat = Chat(
        user_id=user.id,
        title=f"Slack · {contact or m['channel_id']}"[:255],
        model=(mc.base_model if mc else "") or conn.model,
        model_config_id=mc.id if mc else None,
        params=(mc.params if mc else {}) or {},
        folder_id=await _ensure_folder(db, conn, user),
    )
    db.add(chat)
    await db.flush()
    thread = SlackChannelThread(
        connection_id=conn.id, channel_id=m["channel_id"], chat_id=chat.id,
        contact_name=contact or "", is_dm=m.get("is_dm", False),
    )
    db.add(thread)
    await db.flush()
    return thread, chat


def _memory_setup(conn: SlackChannelConnection, chat: Chat, mc: ModelConfig | None, model: str):
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
# Entrega (humanizador: atraso + quebra; Slack não tem "digitando" via bot)
# --------------------------------------------------------------------------- #
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ý0-9\"'(])")


def _humanize_cfg(conn: SlackChannelConnection) -> dict[str, Any]:
    h = conn.humanize or {}
    return {
        "enabled": bool(h.get("enabled")),
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
    return min(base + len(chunk) / 900.0, 12.0)


async def _deliver(conn: SlackChannelConnection, token: str, channel_id: str, text: str) -> None:
    cfg = _humanize_cfg(conn)
    chunks = _split_message(text) if cfg["enabled"] and cfg["split"] else [text]
    for chunk in chunks:
        if cfg["enabled"]:
            wait = _delay_seconds(cfg, chunk)
            if wait > 0:
                await asyncio.sleep(wait)
        await slack_channel_api.post_message(token, channel_id, chunk)


# --------------------------------------------------------------------------- #
# Turno de modelo por mensagem recebida
# --------------------------------------------------------------------------- #
async def _run_one(connection_id: uuid.UUID, bot_user_id: str, msgs: list[dict[str, Any]]) -> None:
    m = msgs[-1]
    from ..chat.orchestrator import MediaOpts, TurnSession, run_turn_guarded
    from ..chat.turn_setup import (
        _brain_setup, _code_mode, _genimage_config, _load_skills, _profile_tz,
        _realtime_datetime, _resolve_guards, _resolve_knowledge, _resolve_provider,
        _usage_record, _user_profile_dict,
    )
    from ..tools.loader import get_sift_for_user

    async with SessionLocal() as db:
        conn = await db.get(SlackChannelConnection, connection_id)
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

        # nome do contato (best-effort — precisa de users:read)
        contact = await slack_channel_api.user_name(token, m["sender_id"])
        thread, chat = await _resolve_thread(db, conn, user, m, contact, mc)

        trigger = ((conn.filters or {}).get("trigger") or "").strip()

        def _clean(t: str) -> str:
            t = _strip_mention(t, bot_user_id)
            if trigger and t.lower().startswith(trigger.lower()):
                return t[len(trigger):].strip() or t
            return t

        text = "\n".join(c for c in (_clean(x["text"]) for x in msgs) if c.strip())

        rows = list(await db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc()).limit(channel_media.history_limit(conn))
        ))
        history = [{"role": r.role, "content": r.content} for r in reversed(rows) if r.content]
        shown = f"{contact}: {text}" if not m.get("is_dm") and contact else text
        db.add(Message(chat_id=chat.id, role="user", content=shown))

        sift = await get_sift_for_user(db, user.id, channel_media.sift_view(mc))
        skills = await _load_skills(db, user, mc)
        guards = await _resolve_guards(db, user, mc)
        genimage = await _genimage_config(db, user, mc)
        where = "a DM" if m.get("is_dm") else "a Slack channel"
        extra_parts = [
            f"You are replying on Slack (workspace '{conn.team or conn.label}') in {where}. "
            f"Answer as a Slack message: concise, plain text; you may use simple markdown "
            f"(bold, lists, `code`) but avoid big headings/tables. Match the contact's language."
        ]
        if (conn.system_prompt or "").strip():
            extra_parts.append(conn.system_prompt.strip())
        extra_system = "\n\n".join(extra_parts)

        await db.commit()

        content = ""
        usage = reasoning = tool_events = None
        error = None
        try:
            mem_chat_id, mem_agent_id, mem_opts = _memory_setup(conn, chat, mc, model)
            knowledge = _resolve_knowledge(chat, mc, user)
            brain = await _brain_setup(db, user, chat, mc)
            async for ev in run_turn_guarded(
                guards=guards, api_key=api_key, model=model, history=history, user_text=text,
                chat_system_prompt=mc.system_prompt if mc else None,
                params=(mc.params if mc else {}) or {},
                base_url=base_url,
                knowledge=knowledge, brain=brain,
                realtime_datetime=_realtime_datetime(mc),
                session=TurnSession(
                    user_id=str(user.id), background=True,
                    chat_id=mem_chat_id, agent_id=mem_agent_id,
                    user_profile=_user_profile_dict(user),
                    user_tz=_profile_tz(user),
                ),
                sift=sift, code_mode=_code_mode(mc),
                skills=skills, use_context=True, extra_system=extra_system,
                extra_breakdown={"channel": len(extra_system)},
                memory=mem_opts,
                media=MediaOpts(genimage=genimage),
            ):
                if ev["type"] == "done":
                    content = ev.get("content", "")
                    usage = ev.get("usage"); reasoning = ev.get("reasoning"); tool_events = ev.get("tool_events")
                elif ev["type"] == "error":
                    error = ev.get("message") or "Falha no modelo"
        except Exception as exc:  # noqa: BLE001
            error = _err_text(exc)

        # gráfico/imagem do turno -> anexo de verdade no canal
        media = await channel_media.collect(tool_events)
        # imagem da Base de Conhecimento colada como markdown -> mídia (o link local
        # seria inútil no Slack); o texto segue sem o markdown
        out_text, kb_media = await channel_media.extract_content_images(content)
        media.extend(kb_media)

        # resposta só com imagem é legítima ("me faz um gráfico"): não é "vazia"
        if error or not (out_text.strip() or media):
            conn.state = {**(conn.state or {}), "last_error": error or "Resposta vazia"}
            await db.commit()
            logger.warning("slack: turno falhou (%s): %s", conn.id, error)
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
            if out_text.strip():
                await _deliver(conn, token, m["channel_id"], out_text)
            for item in media:
                await slack_channel_api.upload_file(
                    token, m["channel_id"], item["data"], item["filename"], item["caption"],
                )
            conn.state = {**(conn.state or {}), "last_error": None,
                          "last_event_at": datetime.now(timezone.utc).isoformat()}
        except Exception as exc:  # noqa: BLE001
            conn.state = {**(conn.state or {}), "last_error": f"Falha ao enviar: {_err_text(exc)}"}
            logger.warning("slack: envio falhou (%s): %s", conn.id, exc)
        await db.commit()


async def handle_message(connection_id: uuid.UUID, event: dict[str, Any], bot_user_id: str) -> None:
    """Processa UMA mensagem: filtra e roda o turno (sessão própria em _run_one)."""
    m = parse_message(event, bot_user_id)
    if m is None:
        return
    async with SessionLocal() as db:
        conn = await db.get(SlackChannelConnection, connection_id)
        if conn is None or not conn.enabled:
            return
        ok, reason = passes_filters(conn, m)
        debounce = conn.debounce_seconds or 0
    if not ok:
        logger.info("slack: mensagem filtrada (%s): %s", connection_id, reason)
        return
    await inbound_batch.submit(
        convo=f"{connection_id}|{m['channel_id']}",
        sender=str(m.get("sender_id") or ""),
        msg=m,
        seconds=debounce,
        runner=lambda batch: _run_one(connection_id, bot_user_id, batch),
    )
