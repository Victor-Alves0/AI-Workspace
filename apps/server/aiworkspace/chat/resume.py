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


_IDLE_WAIT_TRIES = 120   # ~120 × 1s: teto p/ esperar a geração corrente terminar


async def _wait_idle(chat_id: str) -> bool:
    """Espera o chat ficar sem geração ativa (teto curto). True = ocioso."""
    import asyncio

    for _ in range(_IDLE_WAIT_TRIES):
        g = generation.get_active(chat_id)
        if g is None or g.done:
            return True
        await asyncio.sleep(1.0)
    return False


async def resume_chat_turn(
    chat_id: str | None, injected_text: str, *,
    notify_title: str, notify_body: str = "",
    already_persisted: bool = False, notify: bool = True,
    queued_texts: list[str] | None = None,
    _attempt: int = 0,
) -> None:
    """Dispara um turno novo em `chat_id` com `injected_text` (nota do usuário). Se o chat
    já tem uma geração ativa, NÃO faz nada (o chamador — reaper — só chega aqui quando o
    chat está ocioso, mas checamos de novo por segurança).

    `already_persisted=True` (drenagem de fila): as mensagens já foram persistidas pela
    rota de envio e já foram DRENADAS da fila pelo chamador — passe-as em `queued_texts`.
    Não re-persiste: remove-as do histórico (por conteúdo) e usa a junção delas como
    entrada. A idempotência vem da drenagem (`drain_queue` esvazia a lista), não de
    inspecionar o banco.
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
                # `texts` são as mensagens que o usuário enviou DURANTE a geração: a rota já
                # as persistiu e quem chamou aqui já as DRENOU da fila. A drenagem
                # (`drain_queue`) é o ponto de idempotência — ela esvazia a lista no loop
                # single-thread, então quem drenar primeiro ganha e um segundo disparo
                # recebe [] e nem chega aqui.
                #
                # Antes reconstruíamos a entrada pegando as mensagens 'user' do FIM do
                # histórico. Isso NUNCA funcionava: a resposta do turno anterior é
                # persistida ANTES desta continuação rodar, então o histórico fica
                # [user, user_enfileirada, assistant] — a enfileirada não está no fim, a
                # reconstrução voltava vazia e a mensagem do usuário era DESCARTADA em
                # silêncio (o modo "fila" do steering nunca continuava).
                texts = [t for t in (queued_texts or [injected_text]) if (t or "").strip()]
                if not texts:
                    return
                # tira do histórico as mensagens que são a ENTRADA deste turno (casando o
                # conteúdo, da mais recente p/ a mais antiga) — senão iriam duplicadas ao
                # modelo: uma vez no histórico e outra como a pergunta atual.
                restantes = list(convo)
                for t in reversed(texts):
                    alvo = t.strip()
                    for i in range(len(restantes) - 1, -1, -1):
                        m = restantes[i]
                        if m.role == "user" and (m.content or "").strip() == alvo:
                            restantes.pop(i)
                            break
                history = [{"role": m.role, "content": m.content} for m in restantes]
                injected_text = "\n\n".join(texts).strip()
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
                from .. import bg
                bg.spawn(send_to_user(user.id, notify_title, notify_body or content.strip(), "/"))

        # steer/fila também valem na continuação (steering durante o wake, encadear filas)
        _genbox: dict = {}

        def _steer_drain() -> list[str]:
            g = _genbox.get("gen")
            return g.drain_steer() if g is not None else []

        async def _on_queue(texts: list[str]) -> None:
            # `texts` já vêm DRENADOS pelo driver (generation._driver) — passa a lista
            # p/ o turno saber exatamente o que remover do histórico.
            await resume_chat_turn(
                str(cid), "\n\n".join(texts), queued_texts=list(texts),
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
        # CLAIM atômico (fim do setup): entre o guard inicial (get_active lá em cima) e aqui
        # houve muitos awaits (prepare_turn, auto-compactação, leituras de banco). Nesse meio
        # tempo outra geração pode ter começado — outro wake (o reaper do exec_jobs e o
        # _ready_poller do preview disparam independentemente no mesmo chat) OU um envio do
        # usuário. Se começou, NÃO abrimos uma 2ª (custo dobrado): enfileiramos a nota na
        # ativa → ela vira um turno de continuação ao fim (respondido lendo do banco), sem
        # perder a mensagem nem duplicar o turno. get_active + generation.start abaixo são
        # atômicos (loop single-thread, sem await entre eles); o start também é single-flight
        # como rede de segurança. Resíduo raro: se a ativa foi aberta sem on_queue
        # (continue/regenerate), a continuação não dispara — recuperável (o usuário reenvia).
        existing = generation.get_active(str(cid))
        if existing is not None and not existing.done:
            if _attempt >= 1:
                logger.warning("resume: chat %s seguiu ocupado; desisto do wake", cid)
                return
            # NÃO enfileirar aqui: `enqueue` é só memória (quem persiste é a rota de envio),
            # e a continuação da fila reconstrói a entrada a partir do BANCO — uma nota não
            # persistida sumiria em silêncio. Espera ficar ocioso e REFAZ o setup do zero:
            # o histórico mudou (a resposta da geração corrente entrou), então reaproveitar
            # o `source` já montado mandaria um histórico velho ao modelo.
            logger.info("resume: geração ativa em %s no fim do setup — refazendo", cid)
            if not await _wait_idle(str(cid)):
                logger.warning("resume: chat %s ocupado além do teto; desisto do wake", cid)
                return
            await resume_chat_turn(
                chat_id, injected_text, notify_title=notify_title, notify_body=notify_body,
                already_persisted=already_persisted, notify=notify,
                queued_texts=queued_texts, _attempt=_attempt + 1,
            )
            return
        _genbox["gen"] = generation.start(str(cid), source, _finish, on_queue=_on_queue)
    except Exception:  # noqa: BLE001 - wake é best-effort
        logger.exception("resume_chat_turn falhou (chat %s)", chat_id)
