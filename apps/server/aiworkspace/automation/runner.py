"""Execução de uma automação.

`run_automation` é o ponto de entrada (usado pelo scheduler e pela rota de teste).
Ele abre a própria sessão, resolve o que precisa e delega:
  - kind="scheduled" -> `_run_scheduled`: roda um turno de modelo (reusa `run_turn`)
    e grava a resposta no chat alvo + uma notificação.
  - kind="monitor"   -> `_run_monitor`: (Fase 2) checa o watcher determinístico.

Tudo isolado em try/except pelo chamador; aqui focamos na lógica de um disparo.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from .. import tracing
from ..chat.turn_setup import _code_mode, _load_skills, _usage_record
from ..chat.orchestrator import TurnSession, run_turn
from ..db import SessionLocal
from ..models import Automation, AutomationRun, Chat, Message, ModelConfig, Notification, User, WhatsAppConnection
from ..providers import openrouter
from ..search import web_search
from ..secrets_service import (
    ALPHAVANTAGE_KEY,
    BRAVE_KEY,
    FINNHUB_KEY,
    OPENROUTER_KEY,
    TAVILY_KEY,
    get_secret,
)
from ..tools import sift_service
from ..tools.loader import get_sift_for_user
from ..usage_service import usage_event_from_record
from . import watchers

logger = logging.getLogger(__name__)

# guard em memória contra execuções sobrepostas da MESMA automação — compartilhado
# entre o scheduler e o disparo manual (/run). "deny wins": se já roda, pula.
_running: set = set()


def is_running(automation_id) -> bool:
    return automation_id in _running


def _effective_model_config(automation: Automation, mc: ModelConfig | None) -> Any:
    """Config efetiva de ferramentas do turno.

    A automação pode ter a PRÓPRIA seleção de ferramentas + fixadas (pin), para
    dar mais assertividade àquela instrução. Quando ela tem tool_ids próprios,
    montamos um objeto leve que o `get_sift_for_user` entende (ele só lê atributos);
    senão usamos o modelo personalizado como está.
    """
    if not automation.tool_ids:
        return mc
    base_sift = dict((mc.sift_config if mc else None) or {})
    if automation.pinned_tool_ids:
        base_sift["pinned"] = automation.pinned_tool_ids
    return SimpleNamespace(
        tools_enabled=True,
        tool_ids=list(automation.tool_ids),
        filter_config=(mc.filter_config if mc else {}) or {},
        sift_config=base_sift,
        code_mode=bool(getattr(mc, "code_mode", False)),
    )


def _apply_chat_ttl(automation: Automation, chat: Chat) -> None:
    """"Duração do Chat" da automação: TTL deslizante (renova a cada escrita) ou
    visualização única (o front apaga quando o usuário abre e sai)."""
    ttl = (automation.options or {}).get("chat_ttl")
    if ttl == "view_once":
        chat.view_once = True
        chat.expires_at = None
        return
    try:
        hours = float(ttl)
    except (TypeError, ValueError):
        return
    if hours > 0:
        chat.expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        chat.view_once = False


async def _resolve_target_chat(
    db, automation: Automation, user: User, model: str, mc: ModelConfig | None
) -> Chat:
    """Chat de destino conforme `target.mode`: novo a cada disparo, reutilizar um
    (criado uma vez) ou um chat existente escolhido pelo usuário."""
    target = automation.target or {}
    mode = target.get("mode") or "new_each"

    if mode == "existing" and target.get("chat_id"):
        try:
            cid = uuid.UUID(str(target["chat_id"]))
            chat = await db.get(Chat, cid)
            if chat is not None and chat.user_id == user.id:
                return chat  # chat escolhido pelo usuário: sem TTL (não é da automação)
        except (ValueError, TypeError):
            pass  # cai para criar um novo

    if mode == "reuse" and (automation.state or {}).get("chat_id"):
        try:
            cid = uuid.UUID(str(automation.state["chat_id"]))
            chat = await db.get(Chat, cid)
            if chat is not None and chat.user_id == user.id:
                _apply_chat_ttl(automation, chat)  # TTL deslizante: renova a cada escrita
                return chat
        except (ValueError, TypeError):
            pass

    chat = Chat(
        user_id=user.id,
        title=automation.title[:255] or "Automação",
        model=model,
        model_config_id=mc.id if mc else None,
        params=(mc.params if mc else {}) or {},
    )
    _apply_chat_ttl(automation, chat)
    db.add(chat)
    await db.flush()  # garante chat.id
    if mode == "reuse":
        automation.state = {**(automation.state or {}), "chat_id": str(chat.id)}
    return chat


async def _run_scheduled(db, automation: Automation, user: User) -> dict[str, Any]:
    from ..budget_service import budget_state
    if (await budget_state(db, user)).get("blocked"):
        raise RuntimeError("Orçamento mensal atingido (automação pausada)")
    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    if not api_key:
        raise RuntimeError("Chave do OpenRouter não configurada")

    mc = None
    if automation.model_config_id:
        mc = await db.get(ModelConfig, automation.model_config_id)
        if mc is not None and mc.user_id != user.id:
            mc = None
    model = automation.model or (mc.base_model if mc else "")
    if not model:
        raise RuntimeError("Automação sem modelo definido")

    chat = await _resolve_target_chat(db, automation, user, model, mc)

    eff = _effective_model_config(automation, mc)
    sift = await get_sift_for_user(db, user.id, eff)
    skills = await _load_skills(db, user, mc)

    instructions = automation.instructions or ""

    # "Contexto do Chat" (opção da automação; padrão DESLIGADO): cada disparo é
    # independente — no modo "reuse"/"existing" o histórico cresce a cada execução
    # e reenviá-lo faria o custo subir sem parar. Ligado, o modelo vê as últimas
    # mensagens do chat alvo (limitadas) e pode dar continuidade ao que já fez.
    # Carregado ANTES de registrar a instrução (autoflush a duplicaria no histórico).
    use_context = bool((automation.options or {}).get("use_context"))
    history: list[dict[str, Any]] = []
    if use_context:
        rows = list(await db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc())
            .limit(30)
        ))
        history = [{"role": m.role, "content": m.content} for m in reversed(rows) if m.content]

    # registra a instrução como mensagem do usuário (transcrição legível do chat).
    # NÃO commita aqui: tudo (chat novo + msg do usuário + resposta + notificação)
    # é persistido de uma vez em run_automation. Se o modelo falhar, nada fica órfão.
    db.add(Message(chat_id=chat.id, role="user", content=instructions))

    system_prompt = mc.system_prompt if mc else None
    params = (mc.params if mc else {}) or {}
    # "Raciocínio" da automação (options.reasoning): sobrepõe o do modelo.
    # Ausente => vale o que o modelo personalizado define; "off" remove a chave
    # (o orchestrator injeta {"enabled": False} quando ela falta).
    effort = (automation.options or {}).get("reasoning")
    if effort in ("low", "medium", "high"):
        params = {**params, "reasoning": {"effort": effort}}
    elif effort == "off":
        params = {k: v for k, v in params.items() if k != "reasoning"}
    # respeita o off-switch global (allow_code_mode) + tools_enabled, como no chat
    code_mode = _code_mode(eff)
    # sem navegador aqui: preferimos o fuso IANA salvo no profile (acompanha horário
    # de verão); o tz_offset gravado no schedule na criação fica de fallback.
    from ..chat.turn_setup import _profile_tz
    user_tz = _profile_tz(user)
    try:
        tz_off = int((automation.schedule or {}).get("tz_offset"))
    except (TypeError, ValueError):
        tz_off = None

    assistant_content = ""
    assistant_usage: dict | None = None
    assistant_reasoning: dict | None = None
    assistant_tools: list | None = None
    async for event in run_turn(
        api_key=api_key,
        model=model,
        history=history,
        user_text=instructions,
        chat_system_prompt=system_prompt,
        params=params,
        # autônomo: tools pulam revisão interativa; chat_id=None => sem mem0 em
        # jobs de fundo (mais barato/previsível)
        session=TurnSession(user_id=str(user.id), user_tz=user_tz, user_tz_offset=tz_off,
                            background=True),
        sift=sift,
        code_mode=code_mode,
        skills=skills,
        use_context=use_context,
    ):
        if event["type"] == "done":
            assistant_content = event.get("content", "")
            assistant_usage = event.get("usage")
            assistant_reasoning = event.get("reasoning")
            assistant_tools = event.get("tool_events")
        elif event["type"] == "error":
            raise RuntimeError(event.get("message") or "Falha no modelo")

    if not assistant_content:
        raise RuntimeError("O modelo não retornou conteúdo")

    rec = _usage_record(assistant_usage, model, mc)
    msg = Message(
        chat_id=chat.id,
        role="assistant",
        content=assistant_content,
        tokens=rec["total_tokens"] or None,
        cost=rec["cost"] or None,
        usage=rec,
        reasoning=assistant_reasoning,
        tool_events=assistant_tools,
    )
    db.add(msg)
    await db.flush()
    ev = usage_event_from_record(user.id, chat.id, msg.id, rec)
    if ev is not None:
        db.add(ev)
    db.add(
        Notification(
            user_id=user.id,
            automation_id=automation.id,
            title=automation.title,
            body=assistant_content.strip()[:500],
            chat_id=chat.id,
            message_id=msg.id,
        )
    )
    return {
        "chat_id": str(chat.id), "message_id": str(msg.id),
        "text": assistant_content, "cost": rec["cost"] or None,
    }


async def _run_reminder(db, automation: Automation, user: User) -> dict[str, Any]:
    """Lembrete pontual: entrega a mensagem LITERAL no chat alvo + notificação.
    Não chama o modelo (custo zero) — o texto é o próprio lembrete."""
    mc, model = await _resolve_model(db, automation, user)
    chat = await _resolve_target_chat(db, automation, user, model or "", mc)
    body = (automation.instructions or automation.title or "Lembrete").strip()
    msg = Message(chat_id=chat.id, role="assistant", content=body)
    db.add(msg)
    await db.flush()
    db.add(
        Notification(
            user_id=user.id,
            automation_id=automation.id,
            title=automation.title,
            body=body[:500],
            chat_id=chat.id,
            message_id=msg.id,
        )
    )
    return {"chat_id": str(chat.id), "message_id": str(msg.id), "one_shot": True, "text": body}


async def _resolve_model(db, automation: Automation, user: User) -> tuple[Any, str]:
    """(model_config | None, model_base) da automação."""
    mc = None
    if automation.model_config_id:
        mc = await db.get(ModelConfig, automation.model_config_id)
        if mc is not None and mc.user_id != user.id:
            mc = None
    return mc, (automation.model or (mc.base_model if mc else ""))


async def _run_monitor(db, automation: Automation, user: User) -> dict[str, Any]:
    # dependências: busca na web + finanças (determinísticas) + LLM barato p/
    # avaliar condição/redigir o aviso (só quando há mudança).
    tavily = await get_secret(db, user.id, TAVILY_KEY)
    brave = await get_secret(db, user.id, BRAVE_KEY)
    search_cfg = sift_service.search_config_from_secrets(tavily, brave, None)

    async def ws(q: str) -> list[dict]:
        return await web_search(q, search_cfg)

    finnhub = await get_secret(db, user.id, FINNHUB_KEY)
    alpha = await get_secret(db, user.id, ALPHAVANTAGE_KEY)
    finance_cfg = sift_service.finance_config_from_secrets(
        finnhub, alpha, (automation.watcher_config or {}).get("finance")
    )
    finance_cfg.web_search = ws

    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    mc, model = await _resolve_model(db, automation, user)

    async def llm(system: str, user_text: str, max_tokens: int = 200) -> str:
        if not api_key or not model:
            return ""
        try:
            return await openrouter.complete(
                api_key, model,
                [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
                params={"max_tokens": max_tokens, "temperature": 0.2}, timeout=60.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("monitor llm falhou: %s", exc)
            return ""

    deps = watchers.WatcherDeps(web_search=ws, finance_cfg=finance_cfg, llm=llm)
    result = await watchers.check(
        automation.watcher_type or "", automation.watcher_config or {}, automation.state or {}, deps
    )

    # persiste o novo estado sempre (linha de base / última comparação)
    if isinstance(result.get("new_state"), dict):
        automation.state = result["new_state"]

    if result.get("error"):
        raise RuntimeError(result["error"])
    if not result.get("changed"):
        return {"changed": False}

    summary_input = result.get("summary_input") or ""
    body = summary_input
    # redação opcional pelo LLM (a instrução guia o tom); fallback = texto extraído
    if summary_input and api_key and model:
        drafted = await llm(
            "You are a monitoring assistant. Write a short, clear notification to the user "
            "in the user's language (Portuguese), based on the detected update. Be direct.",
            f"Instruction: {automation.instructions or ''}\n\nDetected update:\n{summary_input}",
            300,
        )
        if drafted.strip():
            body = drafted.strip()

    chat = await _resolve_target_chat(db, automation, user, model or "", mc)
    msg = Message(chat_id=chat.id, role="assistant", content=body)
    db.add(msg)
    await db.flush()
    db.add(
        Notification(
            user_id=user.id,
            automation_id=automation.id,
            title=automation.title,
            body=body[:500],
            chat_id=chat.id,
            message_id=msg.id,
        )
    )
    return {"changed": True, "chat_id": str(chat.id), "message_id": str(msg.id), "text": body}


async def _deliver_whatsapp(db, automation: Automation, user: User, text: str | None) -> None:
    """Se a automação estiver configurada para enviar ao WhatsApp, resolve a conexão
    e os destinatários e dispara o envio em BACKGROUND (o humanizador tem atrasos —
    não pode segurar a transação da automação)."""
    wa = (automation.target or {}).get("whatsapp") or {}
    if not wa.get("enabled") or not wa.get("connection_id") or not (text or "").strip():
        return
    from ..integrations import whatsapp_service
    try:
        conn = await db.get(WhatsAppConnection, uuid.UUID(str(wa["connection_id"])))
    except (ValueError, TypeError):
        return
    if conn is None or conn.user_id != user.id or not conn.enabled:
        logger.warning("automação %s: conexão WhatsApp inválida/desligada", automation.id)
        return
    recipients = await whatsapp_service.resolve_recipients(db, conn, wa)
    if not recipients:
        logger.info("automação %s: sem destinatários no WhatsApp", automation.id)
        return
    asyncio.create_task(whatsapp_service.broadcast(conn.id, recipients, text.strip()))


async def _deliver_telegram(db, automation: Automation, user: User, text: str | None) -> None:
    """Entrega ao Telegram (se configurado): resolve a conexão + destinatários e
    dispara o envio em background. Espelha `_deliver_whatsapp`."""
    tg = (automation.target or {}).get("telegram") or {}
    if not tg.get("enabled") or not tg.get("connection_id") or not (text or "").strip():
        return
    from ..integrations import telegram_service
    from ..models import TelegramConnection
    try:
        conn = await db.get(TelegramConnection, uuid.UUID(str(tg["connection_id"])))
    except (ValueError, TypeError):
        return
    if conn is None or conn.user_id != user.id or not conn.enabled:
        logger.warning("automação %s: conexão Telegram inválida/desligada", automation.id)
        return
    recipients = await telegram_service.resolve_recipients(db, conn, tg)
    if not recipients:
        logger.info("automação %s: sem destinatários no Telegram", automation.id)
        return
    asyncio.create_task(telegram_service.broadcast(conn.id, recipients, text.strip()))


async def _record_run(
    automation_id: uuid.UUID, user_id: uuid.UUID, *, status: str, trigger: str,
    text: str | None = None, error: str | None = None,
    chat_id: str | None = None, message_id: str | None = None, cost: float | None = None,
) -> None:
    """Grava uma linha no histórico de execuções (sessão própria — sobrevive a
    rollback do disparo). Best-effort: nunca deve derrubar a automação."""
    def _uuid(v):
        try:
            return uuid.UUID(str(v)) if v else None
        except (ValueError, TypeError):
            return None
    try:
        async with SessionLocal() as db:
            db.add(AutomationRun(
                automation_id=automation_id, user_id=user_id,
                status=status, trigger=trigger,
                text=(text or None) and text[:2000], error=(error or None) and error[:2000],
                chat_id=_uuid(chat_id), message_id=_uuid(message_id), cost=cost,
            ))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("falha ao gravar histórico da automação %s", automation_id)


async def run_automation(automation_id: uuid.UUID, *, trigger: str = "scheduled") -> dict[str, Any]:
    """Executa uma automação pelo id (sessão própria). Levanta em caso de erro —
    o chamador (scheduler/rota) decide como registrar. Guardado contra execução
    sobreposta (scheduler + /run manual simultâneos). `trigger` distingue o disparo
    agendado do manual ("Testar") no histórico."""
    if automation_id in _running:
        return {"skipped": "already_running"}
    _running.add(automation_id)
    # o `discard` mora AQUI, no mesmo escopo do `add`: se qualquer coisa entre a
    # marcação e o corpo falhasse, a automação ficaria marcada como "rodando" para
    # sempre e seria silenciosamente pulada em todos os disparos seguintes.
    try:
        with tracing.start_trace(f"automation:{automation_id}", kind="automation",
                                 path=f"automation/{trigger}"):
            return await _run_automation_body(automation_id, trigger)
    finally:
        _running.discard(automation_id)


async def _run_automation_body(automation_id: uuid.UUID, trigger: str) -> dict[str, Any]:
    user_id: uuid.UUID | None = None
    try:
        async with SessionLocal() as db:
            automation = await db.get(Automation, automation_id)
            if automation is None:
                return {"skipped": "not_found"}
            user = await db.get(User, automation.user_id)
            if user is None:
                return {"skipped": "no_user"}
            user_id = user.id

            try:
                if automation.kind == "monitor":
                    result = await _run_monitor(db, automation, user)
                elif automation.kind == "reminder":
                    result = await _run_reminder(db, automation, user)
                else:
                    result = await _run_scheduled(db, automation, user)
                await db.commit()
            except Exception as exc:  # registra a falha no histórico e re-levanta
                await _record_run(
                    automation_id, user.id, status="error", trigger=trigger,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise

            # histórico do disparo bem-sucedido (monitor sem mudança = "no_change")
            no_change = automation.kind == "monitor" and not result.get("changed")
            await _record_run(
                automation_id, user.id,
                status="no_change" if no_change else "ok", trigger=trigger,
                text=result.get("text"), chat_id=result.get("chat_id"),
                message_id=result.get("message_id"), cost=result.get("cost"),
            )
            # entrega + notificação (monitor só quando houve mudança)
            if automation.kind != "monitor" or result.get("changed"):
                try:
                    await _deliver_whatsapp(db, automation, user, result.get("text"))
                except Exception:  # noqa: BLE001 - entrega não pode falhar a automação
                    logger.exception("automação %s: falha ao entregar no WhatsApp", automation_id)
                try:
                    await _deliver_telegram(db, automation, user, result.get("text"))
                except Exception:  # noqa: BLE001
                    logger.exception("automação %s: falha ao entregar no Telegram", automation_id)
                # notificação push no navegador (best-effort, em background)
                if result.get("text"):
                    from ..push_service import send_to_user
                    asyncio.create_task(send_to_user(user.id, automation.title, result["text"], "/"))
            return result
    finally:
        # libera assim que o corpo termina; `run_automation` repete o discard como
        # rede de segurança (é idempotente num set — ao contrário do contador de
        # vagas da API, onde liberar duas vezes seria bug).
        _running.discard(automation_id)
