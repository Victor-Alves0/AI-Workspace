"""Turno de continuação disparado por um EVENTO DE FUNDO (não por uma request do usuário).

Hoje o único disparador é um job de `exec_jobs` que terminou: o agente soltou um comando
longo (download/build), encerrou o turno, e agora "acorda" pra continuar com o resultado.
`resume_chat_turn` injeta uma nota como mensagem do usuário, roda `run_turn` via
`generation.start` (ao vivo/retomável — o usuário vê continuar se o chat estiver aberto) e
notifica (in-app + push). Best-effort: loga e sai em erro, nunca levanta pro chamador.

Reusa os helpers de montagem de turno de [turn_setup.py] (os mesmos do send/continue), então
não duplica a resolução de modelo/sift/skills/conhecimento/cérebro/mídia.
"""

from __future__ import annotations

import logging

from ..db import SessionLocal
from ..models import Chat, Message, Notification, User
from ..usage_service import usage_event_from_record
from . import artifacts as artifacts_service
from . import generation
from .orchestrator import TurnSession, run_turn_guarded
from .turn_setup import (
    _artifacts_enabled,
    _artifacts_kwargs,
    _brain_setup,
    _code_mode,
    _flag_budget,
    _mem_agent_id,
    _media_opts,
    _memory_opts,
    _ordered_messages,
    _prepare_turn,
    _realtime_datetime,
    _resolve_guards,
    _resolve_knowledge,
    _session_tz,
    _skill_learning,
    _usage_record,
    _use_context,
    _user_profile_dict,
)

logger = logging.getLogger(__name__)


async def resume_chat_turn(
    chat_id: str | None, injected_text: str, *,
    notify_title: str, notify_body: str = "",
    already_persisted: bool = False, notify: bool = True,
) -> None:
    """Dispara um turno novo em `chat_id` com `injected_text` (nota do usuário). Se o chat
    já tem uma geração ativa, NÃO faz nada (o chamador — reaper — só chega aqui quando o
    chat está ocioso, mas checamos de novo por segurança).

    `already_persisted=True` (drenagem de fila): as mensagens já estão no banco como
    'user' no fim do histórico — não re-persiste; remove-as do histórico e usa o
    `injected_text` (a junção delas) como entrada, reconstruindo um turno normal.
    `notify=False` silencia a notificação (o usuário está presente, vendo ao vivo)."""
    if not chat_id:
        return
    if generation.get_active(chat_id) and not generation.get_active(chat_id).done:
        logger.info("resume ignorado: chat %s já tem geração ativa", chat_id)
        return
    try:
        import uuid as _uuid
        cid = _uuid.UUID(str(chat_id))
        async with SessionLocal() as db:
            chat = await db.get(Chat, cid)
            if chat is None:
                return
            user = await db.get(User, chat.user_id)
            if user is None or not chat.model:
                return
            from ..budget_service import budget_state
            if (await budget_state(db, user)).get("blocked"):
                logger.info("resume pausado: orçamento do usuário %s atingido", user.id)
                return
            api_key, base_url, model_config, sift, skills = await _prepare_turn(db, user, chat)
            # guardas de saída valem no wake tanto quanto no send: é o trabalho autônomo
            # continuando — exatamente onde o guarda-juiz de fundamentação deve atuar.
            guards = await _resolve_guards(db, user, model_config)
            model = chat.model
            system_prompt = chat.system_prompt
            params = chat.params or {}
            arts_on = _artifacts_enabled(user)
            # auto-compactação (Claude Code): encolhe o contexto antes de continuar se cresceu
            from . import compaction_service
            await compaction_service.maybe_autocompact(db, user, chat, model_config)
            rows = await _ordered_messages(db, cid)
            convo = [
                m for m in rows
                if m.role in ("user", "assistant") and m.content and not m.compacted
            ]
            if already_persisted:
                # as mensagens enfileiradas já estão no fim como 'user' sem resposta.
                # Reconstrói o input a partir do BANCO (não dos textos passados): assim é
                # idempotente sob corrida — se duas continuações dispararem, a 2ª acha o
                # fim já respondido (sem 'user' pendente) e SAI, sem duplicar o turno.
                trailing = []
                while convo and convo[-1].role == "user":
                    trailing.insert(0, convo.pop())
                if not trailing:
                    return  # já respondido por outra continuação → nada a fazer
                history = [{"role": m.role, "content": m.content} for m in convo]
                injected_text = "\n\n".join((m.content or "") for m in trailing).strip()
            else:
                history = [{"role": m.role, "content": m.content} for m in convo]
                # registra a nota como mensagem do usuário (transcrição legível do chat)
                db.add(Message(chat_id=cid, role="user", content=injected_text))
                await db.commit()
            user_id = str(user.id)
            project_id = str(chat.project_id) if chat.project_id else None
            arts_kwargs = await _artifacts_kwargs(db, cid, user, arts_on, model_config)
            knowledge = _resolve_knowledge(chat, model_config, user)
            brain = await _brain_setup(db, user, chat, model_config)
            memory = _memory_opts(chat, model_config, user)
            media = await _media_opts(db, user, model_config)

        async def _finish(collected: dict, emit) -> None:
            content = collected["content"] or (collected["streamed"] or "").strip()
            if not content:
                return
            reasoning = collected["reasoning"]
            rec = _usage_record(collected["usage"], model, model_config)
            _flag_budget(rec, model_config, user)
            arts_changed: list[str] = []
            async with SessionLocal() as s:
                if arts_on:
                    content, arts_changed = await artifacts_service.extract_and_apply(
                        s, cid, user.id, content
                    )
                m = Message(
                    chat_id=cid, role="assistant", content=content,
                    tokens=rec["total_tokens"] or None, cost=rec["cost"] or None,
                    usage=rec, reasoning=reasoning, tool_events=collected["tools"],
                    memories_used=collected.get("memories"),
                )
                s.add(m)
                await s.flush()
                ev = usage_event_from_record(user.id, cid, m.id, rec)
                if ev is not None:
                    s.add(ev)
                if notify:
                    s.add(Notification(
                        user_id=user.id, title=notify_title,
                        body=(content.strip() or notify_body)[:500],
                        chat_id=cid, message_id=m.id,
                    ))
                await s.commit()
            if arts_changed:
                await emit({"type": "artifacts", "ids": arts_changed})
            if notify:
                from ..push_service import send_to_user
                import asyncio as _asyncio
                _asyncio.create_task(send_to_user(user.id, notify_title, notify_body or content.strip(), "/"))

        # steer/fila também valem na continuação (steering durante o wake, encadear filas)
        _genbox: dict = {}

        def _steer_drain() -> list[str]:
            g = _genbox.get("gen")
            return g.drain_steer() if g is not None else []

        async def _on_queue(texts: list[str]) -> None:
            await resume_chat_turn(
                str(cid), "\n\n".join(texts),
                notify_title=notify_title, already_persisted=True, notify=False,
            )

        source = run_turn_guarded(
            guards=guards,
            api_key=api_key,
            model=model,
            history=history,
            user_text=injected_text,
            chat_system_prompt=system_prompt,
            params=params,
            base_url=base_url,
            **arts_kwargs,
            session=TurnSession(
                user_id=user_id, user_tz=_session_tz(user, ""), chat_id=str(cid),
                agent_id=_mem_agent_id(model_config, model),
                user_profile=_user_profile_dict(user),
                codespace_project_id=project_id,
                steer_drain=_steer_drain,
            ),
            sift=sift,
            code_mode=_code_mode(model_config),
            skills=skills,
            use_context=_use_context(model_config),
            knowledge=knowledge,
            brain=brain,
            skill_learning=_skill_learning(model_config),
            realtime_datetime=_realtime_datetime(model_config),
            memory=memory,
            media=media,
        )
        _genbox["gen"] = generation.start(str(cid), source, _finish, on_queue=_on_queue)
    except Exception:  # noqa: BLE001 - wake é best-effort
        logger.exception("resume_chat_turn falhou (chat %s)", chat_id)
