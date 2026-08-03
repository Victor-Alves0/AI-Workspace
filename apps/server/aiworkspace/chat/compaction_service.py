"""Núcleo da COMPACTAÇÃO de contexto (resumo não-destrutivo + checkpoint restaurável),
reusável pela rota manual (/compact) e pela AUTO-compactação na entrada do turno.

Auto-compactação = o modelo do Claude Code: quando o contexto se aproxima da janela do
modelo, resume o histórico ANTIGO, mantém as últimas mensagens intactas, e segue. O
`keep_last` é o que difere do manual (que resume tudo). O Ledger da tarefa é a rede de
segurança: objetivo/plano/achados vivem fora das mensagens e sobrevivem à compactação.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Chat, ChatCompaction, Message, ModelConfig, User
from ..providers import openrouter
from ..secrets_service import OPENROUTER_KEY, get_secret
from .turn_setup import _ordered_messages, _resolve_provider

logger = logging.getLogger(__name__)

_COMPACT_SYSTEM = (
    "Você resume conversas preservando o máximo de contexto útil no mínimo de espaço."
)
# Instrução lida pelo MODELO => inglês (padrão do projeto). O RESUMO sai no idioma da
# conversa, porque ele substitui as mensagens no contexto e é lido pelo usuário.
_COMPACT_INSTRUCTION = (
    "Summarize the conversation below concisely but completely, preserving: important "
    "facts and data, decisions made, the user's preferences and personal details, the "
    "current state of the task, and every piece of context needed to continue without "
    "losing anything relevant. Use clear bullet points. Never invent information that is "
    "not in the conversation. Write the summary in the same language as the conversation. "
    "Reply with the summary only."
)


def summary_content(summary: str) -> str:
    return f"📝 **Resumo da conversa anterior (contexto compactado):**\n\n{summary}"


def serialize_message(m: Message) -> dict:
    """Serializa uma mensagem p/ o snapshot do checkpoint (restaurável). Espelha o
    formato do /compact manual — mudou de lá para cá; mantenha os dois em sincronia."""
    return {
        "role": m.role,
        "content": m.content,
        "tool_calls": m.tool_calls,
        "tool_call_id": m.tool_call_id,
        "tokens": m.tokens,
        "cost": m.cost,
        "usage": m.usage,
        "reasoning": m.reasoning,
        "tool_events": m.tool_events,
        "attachments": m.attachments,
        "is_summary": bool(m.is_summary),
        "compacted": bool(m.compacted),
        "speaker": m.speaker,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


async def _summarize(api_key: str, model: str, base_url: str | None,
                     convo: list[Message]) -> str:
    transcript = "\n\n".join(
        f"{'Usuário' if m.role == 'user' else 'Assistente'}: {m.content}" for m in convo
    )
    messages = [
        {"role": "system", "content": _COMPACT_SYSTEM},
        {"role": "user", "content": f"{_COMPACT_INSTRUCTION}\n\n=== CONVERSA ===\n{transcript}"},
    ]
    summary = ""
    async for chunk in openrouter.stream_chat(
        api_key, model, messages, tools=None, params={}, base_url=base_url
    ):
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            if delta.get("content"):
                summary += delta["content"]
    return summary.strip()


async def run_compaction(db: AsyncSession, user: User, chat: Chat, *,
                         keep_last: int = 0, min_convo: int = 3) -> dict | None:
    """Compacta o contexto do chat. Resume as mensagens não-compactadas, mantendo as
    ÚLTIMAS `keep_last` intactas (0 = resume tudo, comportamento do /compact manual).
    Não-destrutivo: cria checkpoint restaurável, marca as antigas `compacted=True`, e
    insere o divisor de resumo no lugar delas. Devolve dict ou None se não há o que
    compactar. Levanta em falha de resumo (o chamador decide: 502 manual / silencioso auto)."""
    chat_id = chat.id
    # modelo auxiliar barato p/ resumir (Config → Chats) senão o modelo da conversa
    iface = (user.profile or {}).get("interface") or {}
    summary_model = (iface.get("compact_model") or "").strip() or chat.model
    api_key, base_url = await _resolve_provider(db, user, summary_model)

    rows = await _ordered_messages(db, chat_id)
    convo = [
        m for m in rows
        if m.role in ("user", "assistant") and (m.content or "").strip() and not m.compacted
    ]
    keep_last = max(0, int(keep_last))
    if keep_last and len(convo) > keep_last:
        to_summarize = convo[:-keep_last]
        kept_first = convo[-keep_last]
    else:
        to_summarize = convo
        kept_first = None
    if len(to_summarize) < min_convo:
        return None  # nada relevante a compactar

    summary = await _summarize(api_key, summary_model, base_url, to_summarize)
    if not summary:
        raise RuntimeError("o modelo não retornou um resumo")

    # snapshot ÍNTEGRO p/ restaurar exatamente este ponto; cadeia de checkpoints
    original_snapshot = [serialize_message(m) for m in rows]
    parent_id = await db.scalar(
        select(ChatCompaction.id).where(
            ChatCompaction.chat_id == chat_id, ChatCompaction.pinned.is_(True))
    )
    # 1ª compactação: ancora um checkpoint "Estado original" (chat inteiro, sem resumo)
    prior = await db.scalar(
        select(ChatCompaction.id).where(ChatCompaction.chat_id == chat_id).limit(1)
    )
    if prior is None:
        original = ChatCompaction(
            chat_id=chat_id, summary="", message_count=len(convo), pinned=False,
            parent_id=None, snapshot=original_snapshot, name="Estado original")
        db.add(original)
        await db.flush()
        parent_id = original.id
    await db.execute(
        update(ChatCompaction).where(ChatCompaction.chat_id == chat_id).values(pinned=False)
    )
    checkpoint = ChatCompaction(
        chat_id=chat_id, summary=summary, message_count=len(to_summarize),
        pinned=True, parent_id=parent_id, snapshot=original_snapshot)
    db.add(checkpoint)

    # tira do contexto SÓ as mensagens antigas (até o corte); as últimas keep_last ficam.
    cutoff = to_summarize[-1].created_at
    await db.execute(
        update(Message)
        .where(Message.chat_id == chat_id, Message.compacted.is_(False),
               Message.created_at <= cutoff)
        .values(compacted=True)
    )
    # o divisor de resumo entra ANTES das mensagens mantidas (created_at logo antes da
    # 1ª mantida) p/ ordenar certo; sem keep_last, vai ao fim (created_at padrão = agora).
    note = Message(
        chat_id=chat_id, role="assistant", content=summary_content(summary),
        is_summary=True, compacted=False,
    )
    if kept_first is not None and kept_first.created_at is not None:
        note.created_at = kept_first.created_at - timedelta(microseconds=1)
    db.add(note)
    await db.commit()
    await db.refresh(checkpoint)
    # medição de primitivo: a compactação preservou a janela de contexto. record_bg:
    # roda no main loop (pré-turno) → offloada o psycopg2 p/ não bloquear o loop.
    try:
        from .. import health_service
        health_service.record_bg("compaction", "fired", severity="info",
                                 detail={"compacted": len(to_summarize)}, chat_id=str(chat.id))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "summary": summary, "compaction_id": str(checkpoint.id),
            "compacted_count": len(to_summarize)}


# --------------------------------------------------------------------------- #
# Auto-compactação: dispara na entrada do turno quando o contexto passa do limiar
# --------------------------------------------------------------------------- #
async def _last_context_tokens(db: AsyncSession, chat_id) -> int:
    """Tamanho REAL do contexto (usage.context_tokens = prompt da 1ª chamada do último
    turno). NÃO usa prompt_tokens: ele é a SOMA cumulativa das iterações do loop agêntico
    (pode dar milhões e não representa o contexto). Exclui o divisor is_summary (sem usage —
    senão logo após compactar leríamos 0 e nunca mais dispararíamos)."""
    m = (await db.scalars(
        select(Message).where(Message.chat_id == chat_id, Message.role == "assistant",
                              Message.compacted.is_(False), Message.is_summary.is_(False))
        .order_by(Message.created_at.desc()).limit(1)
    )).first()
    if m is None or not m.usage:
        return 0
    try:
        # context_tokens é o campo certo; prompt_tokens (soma) só como fallback grosseiro
        return int(m.usage.get("context_tokens") or m.usage.get("prompt_tokens") or 0)
    except (TypeError, ValueError):
        return 0


async def _model_window(db: AsyncSession, user: User, model: str) -> int:
    """Janela de contexto do modelo (context_length do catálogo OpenRouter, cacheado).
    0 se desconhecida (ex.: Ollama/custom) — o chamador usa o fallback."""
    real = model.split("custom:")[-1] if model.startswith("custom:") else model
    try:
        key = await get_secret(db, user.id, OPENROUTER_KEY)
        if not key:
            return 0
        for m in await openrouter.list_models(key):
            if m.get("id") == real:
                return int(m.get("context_length") or 0)
    except Exception:  # noqa: BLE001 - catálogo indisponível → fallback
        return 0
    return 0


async def maybe_autocompact(db: AsyncSession, user: User, chat: Chat,
                            model_config: ModelConfig | None) -> bool:
    """Se o contexto passou do limiar da janela do modelo, compacta ANTES do turno
    (mantendo as últimas N mensagens). Best-effort: nunca levanta. Modelo do Claude Code."""
    s = get_settings()
    if not s.autocompact_enabled:
        return False
    try:
        last_tokens = await _last_context_tokens(db, chat.id)
        if last_tokens <= 0:
            return False
        window = await _model_window(db, user, chat.model) or int(s.autocompact_fallback_window)
        if last_tokens < float(s.autocompact_threshold) * window:
            return False
        res = await run_compaction(db, user, chat,
                                   keep_last=int(s.autocompact_keep_last),
                                   min_convo=max(3, int(s.autocompact_min_messages) - int(s.autocompact_keep_last)))
        if res:
            logger.info("auto-compactação: chat %s (%d tokens > %.0f%% de %d) → resumiu %d msgs",
                        chat.id, last_tokens, s.autocompact_threshold * 100, window,
                        res["compacted_count"])
        return bool(res)
    except Exception:  # noqa: BLE001 - auto-compactação nunca quebra o turno
        logger.exception("auto-compactação falhou (chat %s)", chat.id)
        return False
