"""Rotas de MENSAGENS do chat: envio (SSE), efêmero, parar/retomar o stream,
editar/excluir mensagem, regenerar e continuar. Montado sob o router /chats
(ver routes.py)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import budget_service
from ..auth.deps import require_approved
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import Chat, Message, User
from ..schemas.chat import MessageEdit, MessageOut, SendMessageIn
from ..tools.loader import get_sift_for_user
from ..usage_service import usage_event_from_record
from . import artifacts as artifacts_service
from . import generation
from .orchestrator import TurnSession, run_turn, run_turn_guarded
from .titles import generate_title
from .turn_setup import (
    _artifacts_enabled,
    _artifacts_kwargs,
    _clean_attachments,
    _code_mode,
    _final_message_fields,
    _flag_budget,
    _get_model_config,
    _get_owned_chat,
    _load_skills,
    _make_subagent_runner,
    _media_opts,
    _mem_agent_id,
    _memory_opts,
    _ordered_messages,
    _prepare_attachments,
    _prepare_turn,
    _brain_setup,
    _resolve_guards,
    _realtime_datetime,
    _resolve_knowledge,
    _resolve_provider,
    _skill_learning,
    _resolve_subagents,
    _ref_chats,
    _ref_docs,
    _sse,
    _sse_stream,
    _subagent_opts,
    _subscribe,
    _remember_tz,
    _session_tz,
    _tz_from_header,
    _usage_record,
    _use_context,
    _user_profile_dict,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/ephemeral")
async def ephemeral(
    body: dict,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    """Chat temporário: streama um turno SEM persistir nada no banco."""
    settings = get_settings()
    model = (body.get("model") or "").strip()
    content = (body.get("content") or "").strip()
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione um modelo")
    if not content and not body.get("attachments"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mensagem vazia")
    if len(content) > settings.max_message_chars:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Mensagem excede {settings.max_message_chars} caracteres",
        )
    await budget_service.enforce_or_raise(db, user)  # orçamento pessoal (modo "pausar")

    api_key, base_url = await _resolve_provider(db, user, model)

    model_config = await _get_model_config(db, body.get("model_config_id"), user)
    guards = await _resolve_guards(db, user, model_config)  # guardas valem no temporário também
    sift = await get_sift_for_user(db, user.id, model_config)
    skills = await _load_skills(db, user, model_config, body.get("skill_ids") or [])
    attachments = await _prepare_attachments(body.get("attachments"), model_config)
    raw_history = body.get("history") or []
    if not isinstance(raw_history, list):
        raw_history = []
    history = [
        {"role": m.get("role"), "content": str(m.get("content"))[: settings.max_message_chars]}
        for m in raw_history[-100:]  # só as últimas 100 mensagens
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    user_id = str(user.id)
    # resolvido ANTES do stream: a sessão `db` da request não deve ser usada
    # depois que a resposta começa a ser transmitida
    media = await _media_opts(db, user, model_config, attachments=attachments)
    brain = await _brain_setup(db, user, None, model_config)

    async def event_stream():
        final_usage: dict | None = None
        async for event in run_turn_guarded(
            guards=guards,
            api_key=api_key,
            model=model,
            history=history,
            user_text=content,
            chat_system_prompt=body.get("system_prompt"),
            params=body.get("params") or {},
            base_url=base_url,
            session=TurnSession(user_id=user_id, user_tz=_session_tz(user, user_tz)),
            sift=sift,
            code_mode=_code_mode(model_config),
            skills=skills,
            use_context=_use_context(model_config),
            knowledge=_resolve_knowledge(None, model_config, user),
            brain=brain,
            skill_learning=_skill_learning(model_config),
            realtime_datetime=_realtime_datetime(model_config),
            media=media,
        ):
            if isinstance(event, dict) and event.get("type") == "done":
                final_usage = event.get("usage")
            yield _sse(event)
        # o chat temporário NÃO é persistido, mas o custo real aconteceu: registra o
        # uso no ledger (chat_id=None) p/ a analítica e o orçamento ficarem corretos.
        if final_usage:
            try:
                rec = _usage_record(final_usage, model, model_config)
                async with SessionLocal() as s:
                    uev = usage_event_from_record(user.id, None, None, rec)
                    if uev is not None:
                        s.add(uev)
                        await s.commit()
            except Exception:  # noqa: BLE001 - ledger é best-effort
                logger.warning("ephemeral: falha ao registrar uso no ledger")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: SendMessageIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione um modelo no chat")
    if not (body.content or "").strip() and not body.attachments:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mensagem vazia")
    await budget_service.enforce_or_raise(db, user)  # orçamento pessoal (modo "pausar")
    _remember_tz(user, user_tz)  # canais (sem navegador) usam o fuso salvo aqui

    api_key, base_url = await _resolve_provider(db, user, chat.model)

    # histórico atual (antes da nova mensagem) no formato OpenAI
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    history = [
        {"role": m.role, "content": m.content}
        for m in rows
        if m.role in ("user", "assistant") and m.content and not m.compacted
    ]

    # modelo personalizado do chat (define ferramentas + config por-modelo)
    model_config = await _get_model_config(db, chat.model_config_id, user)

    # "@" no promptbox: roteia ESTE turno a outro agente (ModelConfig) sem alterar o
    # padrão do chat. Passa a valer o modelo/prompt/tools/skills DESSE agente.
    agent_override = None
    if body.agent_model_config_id:
        agent_override = await _get_model_config(db, body.agent_model_config_id, user)
        if agent_override is not None and agent_override.base_model:
            model_config = agent_override
        else:
            agent_override = None

    # persiste a mensagem do usuário (extraindo texto de docs; sem guardar o binário)
    attachments = await _prepare_attachments([a.model_dump() for a in body.attachments], model_config)
    user_msg = Message(
        chat_id=chat.id, role="user", content=body.content, attachments=attachments or None
    )
    db.add(user_msg)
    # primeiro título do chat = início da primeira mensagem (fallback instantâneo)
    first_exchange = chat.title == "Novo Chat" and not history
    if first_exchange:
        chat.title = (body.content[:60] or "Anexo")
    await db.commit()

    # geração de título por IA (opt-in em perfil → Interface): só na 1ª troca
    iface = (user.profile or {}).get("interface") if isinstance(user.profile, dict) else None
    iface = iface if isinstance(iface, dict) else {}
    auto_title = first_exchange and bool(iface.get("auto_title"))
    title_model = (iface.get("title_model") or "").strip()
    title_prompt = iface.get("title_prompt") or ""

    # SIFT do usuário, filtrada pelas ferramentas do modelo personalizado do chat
    sift = await get_sift_for_user(
        db, user.id, model_config,
        codespace_project_id=str(chat.project_id) if chat.project_id else None,
    )
    skills = await _load_skills(db, user, model_config, body.skill_ids)

    # valores efetivos do turno: do agente mencionado (@) ou os padrões do chat
    if agent_override is not None:
        model = agent_override.base_model
        api_key, base_url = await _resolve_provider(db, user, model)
        system_prompt = agent_override.system_prompt
        params = agent_override.params or {}
    else:
        model = chat.model
        system_prompt = chat.system_prompt
        params = chat.params or {}
    user_text = body.content
    user_id = str(user.id)

    arts_on = _artifacts_enabled(user)

    # persiste a resposta e (opt-in) gera o título; roda no driver de background,
    # blindado por `shield` — completa mesmo se o cliente desconectar (F5).
    async def _finish(collected: dict, emit) -> None:
        content_to_save, reasoning = _final_message_fields(collected)
        arts_changed: list[str] = []
        # salva também quando não houve texto mas houve artefato (ex.: imagem nativa)
        if content_to_save or collected["tools"]:
            rec = _usage_record(collected["usage"], model, model_config)
            _flag_budget(rec, model_config, user)
            async with SessionLocal() as s:
                if arts_on and content_to_save:
                    # blocos <artifact> viram linhas versionadas; no texto fica [[artifact:id]]
                    content_to_save, arts_changed = await artifacts_service.extract_and_apply(
                        s, chat_id, user.id, content_to_save
                    )
                m = Message(
                    chat_id=chat_id,
                    role="assistant",
                    content=content_to_save,
                    tokens=rec["total_tokens"] or None,
                    cost=rec["cost"] or None,
                    usage=rec,
                    reasoning=reasoning,
                    tool_events=collected["tools"],
                    memories_used=collected.get("memories"),
                )
                s.add(m)
                await s.flush()
                ev = usage_event_from_record(user.id, chat_id, m.id, rec)
                if ev is not None:
                    s.add(ev)
                await s.commit()
        if arts_changed:
            await emit({"type": "artifacts", "ids": arts_changed})
        # título por IA (1ª troca + opt-in): substitui o fallback de 60 chars.
        if auto_title and collected["content"]:
            new_title = await generate_title(
                api_key, title_model or model, user_text, collected["content"], title_prompt,
                base_url=base_url if not title_model else None,
            )
            if new_title:
                async with SessionLocal() as s:
                    c = await s.get(Chat, chat_id)
                    if c is not None:
                        c.title = new_title
                        await s.commit()
                await emit({"type": "title", "title": new_title})

    guards = await _resolve_guards(db, user, model_config)
    sub_specs, sub_conf = await _resolve_subagents(db, user, model_config)
    sub_runner = _make_subagent_runner(
        db, user, chat_id, sub_conf.get("max_depth", 2),
        pass_context=sub_conf.get("pass_context", False),
        worker_memory=sub_conf.get("worker_memory", False),
    ) if sub_specs else None
    source = run_turn_guarded(
        guards=guards,
        api_key=api_key,
        model=model,
        history=history,
        user_text=user_text,
        chat_system_prompt=system_prompt,
        params=params,
        base_url=base_url,
        **(await _artifacts_kwargs(db, chat_id, user, arts_on, model_config)),
        session=TurnSession(
            user_id=user_id, user_tz=_session_tz(user, user_tz), chat_id=str(chat_id),
            agent_id=_mem_agent_id(model_config, model),
            user_profile=_user_profile_dict(user),
            codespace_project_id=str(chat.project_id) if chat.project_id else None,
        ),
        sift=sift,
        code_mode=_code_mode(model_config),
        skills=skills,
        use_context=_use_context(model_config),
        knowledge=_resolve_knowledge(chat, model_config, user),
        brain=await _brain_setup(db, user, chat, model_config),
        skill_learning=_skill_learning(model_config),
        realtime_datetime=_realtime_datetime(model_config),
        ref_docs=await _ref_docs(db, user, chat, model_config, body.ref_doc_ids),
        ref_chats=await _ref_chats(db, user, body.ref_chat_ids),
        memory=_memory_opts(chat, model_config, user),
        media=await _media_opts(db, user, model_config, attachments=attachments),
        subagent=_subagent_opts(sub_specs, sub_conf, sub_runner),
    )
    # a geração roda em background (desacoplada da request); a resposta abaixo é
    # só um assinante do buffer. F5/desconexão mata o assinante, não a geração.
    gen = generation.start(str(chat_id), source, _finish)
    return _sse_stream(_subscribe(gen))


@router.post("/{chat_id}/stop")
async def stop_generation(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """"Parar" do usuário: cancela a geração em andamento deste chat. O texto já
    transmitido é persistido como resposta parcial (mesmo caminho do shutdown)."""
    await _get_owned_chat(db, chat_id, user)
    gen = generation.get_active(str(chat_id))
    return {"ok": True, "stopped": bool(gen and gen.stop())}


@router.get("/{chat_id}/stream")
async def resume_stream(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Re-assina a geração em andamento de um chat (ex.: o usuário deu F5 no meio
    de uma resposta). Se não houver nada gerando, emite ``idle`` e encerra — o
    front então só carrega as mensagens já persistidas."""
    await _get_owned_chat(db, chat_id, user)
    gen = generation.get_active(str(chat_id))
    if gen is None:

        async def idle():
            yield _sse({"type": "idle"})

        return _sse_stream(idle())
    return _sse_stream(_subscribe(gen))


@router.patch("/{chat_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageEdit,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Edita o conteúdo de uma mensagem (do usuário ou do assistant)."""
    await _get_owned_chat(db, chat_id, user)
    msg = await db.get(Message, message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mensagem não encontrada")
    msg.content = body.content
    await db.commit()
    await db.refresh(msg)
    return msg


@router.delete("/{chat_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Exclui uma mensagem (do usuário ou do assistant)."""
    await _get_owned_chat(db, chat_id, user)
    msg = await db.get(Message, message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mensagem não encontrada")
    await db.delete(msg)
    await db.commit()


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    """Refaz uma resposta do assistant: descarta essa mensagem (e as posteriores)
    e gera uma nova a partir do mesmo prompt do usuário."""
    chat = await _get_owned_chat(db, chat_id, user)
    await budget_service.enforce_or_raise(db, user)  # orçamento pessoal (modo "pausar")
    api_key, base_url, model_config, sift, skills = await _prepare_turn(db, user, chat)

    rows = await _ordered_messages(db, chat_id)
    idx = next((i for i, m in enumerate(rows) if m.id == message_id), None)
    if idx is None or rows[idx].role not in ("assistant", "user"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mensagem não encontrada")

    if rows[idx].role == "user":
        # "Tentar novamente" NA MENSAGEM DO USUÁRIO (ex.: depois de editá-la):
        # a IA pensa a partir dela — a mensagem fica; tudo que veio depois sai.
        user_text = rows[idx].content
        user_attachments = _clean_attachments(rows[idx].attachments or [])
        if not user_text and not user_attachments:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mensagem vazia")
        history = [
            {"role": m.role, "content": m.content}
            for m in rows[:idx]
            if m.role in ("user", "assistant") and m.content and not m.compacted
        ]
        for m in rows[idx + 1:]:
            await db.delete(m)
        await db.commit()
    else:
        prior = rows[:idx]
        # prompt = última mensagem do usuário antes da resposta
        user_text = ""
        user_attachments = []
        cut = len(prior)
        for i in range(len(prior) - 1, -1, -1):
            if prior[i].role == "user":
                user_text = prior[i].content
                user_attachments = _clean_attachments(prior[i].attachments or [])
                cut = i
                break
        if not user_text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sem prompt do usuário para refazer")
        history = [
            {"role": m.role, "content": m.content}
            for m in prior[:cut]
            if m.role in ("user", "assistant") and m.content and not m.compacted
        ]

        # remove a resposta e tudo que veio depois
        for m in rows[idx:]:
            await db.delete(m)
        await db.commit()

    model = chat.model
    system_prompt = chat.system_prompt
    params = chat.params or {}
    user_id = str(user.id)
    arts_on = _artifacts_enabled(user)

    async def _finish(collected: dict, emit) -> None:
        content_to_save, reasoning = _final_message_fields(collected)
        arts_changed: list[str] = []
        # salva também quando não houve texto mas houve artefato (ex.: imagem nativa)
        if content_to_save or collected["tools"]:
            rec = _usage_record(collected["usage"], model, model_config)
            _flag_budget(rec, model_config, user)
            async with SessionLocal() as s:
                if arts_on and content_to_save:
                    content_to_save, arts_changed = await artifacts_service.extract_and_apply(
                        s, chat_id, user.id, content_to_save
                    )
                m = Message(
                    chat_id=chat_id,
                    role="assistant",
                    content=content_to_save,
                    tokens=rec["total_tokens"] or None,
                    cost=rec["cost"] or None,
                    usage=rec,
                    reasoning=reasoning,
                    tool_events=collected["tools"],
                    memories_used=collected.get("memories"),
                )
                s.add(m)
                await s.flush()
                ev = usage_event_from_record(user.id, chat_id, m.id, rec)
                if ev is not None:
                    s.add(ev)
                await s.commit()
        if arts_changed:
            await emit({"type": "artifacts", "ids": arts_changed})

    guards = await _resolve_guards(db, user, model_config)
    sub_specs, sub_conf = await _resolve_subagents(db, user, model_config)
    sub_runner = _make_subagent_runner(
        db, user, chat_id, sub_conf.get("max_depth", 2),
        pass_context=sub_conf.get("pass_context", False),
        worker_memory=sub_conf.get("worker_memory", False),
    ) if sub_specs else None
    source = run_turn_guarded(
        guards=guards,
        api_key=api_key,
        model=model,
        history=history,
        user_text=user_text,
        chat_system_prompt=system_prompt,
        params=params,
        base_url=base_url,
        **(await _artifacts_kwargs(db, chat_id, user, arts_on, model_config)),
        session=TurnSession(
            user_id=user_id, user_tz=_session_tz(user, user_tz), chat_id=str(chat_id),
            agent_id=_mem_agent_id(model_config, model),
            user_profile=_user_profile_dict(user),
            codespace_project_id=str(chat.project_id) if chat.project_id else None,
        ),
        sift=sift,
        code_mode=_code_mode(model_config),
        skills=skills,
        use_context=_use_context(model_config),
        knowledge=_resolve_knowledge(chat, model_config, user),
        brain=await _brain_setup(db, user, chat, model_config),
        skill_learning=_skill_learning(model_config),
        realtime_datetime=_realtime_datetime(model_config),
        memory=_memory_opts(chat, model_config, user),
        media=await _media_opts(db, user, model_config, attachments=user_attachments),
        subagent=_subagent_opts(sub_specs, sub_conf, sub_runner),
    )
    gen = generation.start(str(chat_id), source, _finish)
    return _sse_stream(_subscribe(gen))


@router.post("/{chat_id}/messages/{message_id}/continue")
async def continue_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    """Continua a última resposta do assistant, anexando ao conteúdo existente."""
    chat = await _get_owned_chat(db, chat_id, user)
    await budget_service.enforce_or_raise(db, user)  # orçamento pessoal (modo "pausar")
    api_key, base_url, model_config, sift, skills = await _prepare_turn(db, user, chat)

    rows = await _ordered_messages(db, chat_id)
    idx = next((i for i, m in enumerate(rows) if m.id == message_id), None)
    if idx is None or rows[idx].role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resposta não encontrada")
    if idx != len(rows) - 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Só é possível continuar a última resposta"
        )

    history = [
        {"role": m.role, "content": m.content}
        for m in rows
        if m.role in ("user", "assistant") and m.content and not m.compacted
    ]
    user_text = (
        "Continue sua resposta anterior exatamente de onde parou, "
        "sem repetir nada do que já foi escrito e sem preâmbulos."
    )

    model = chat.model
    system_prompt = chat.system_prompt
    params = chat.params or {}
    user_id = str(user.id)

    arts_on = _artifacts_enabled(user)

    async def _finish(collected: dict, emit) -> None:
        content = collected["content"] or (collected["streamed"] or "").strip()
        if not content:
            return
        reasoning = collected["reasoning"]
        tools = collected["tools"]
        rec = _usage_record(collected["usage"], model, model_config)
        _flag_budget(rec, model_config, user)
        # ledger: só o DELTA desta continuação (o evento do original já foi gravado);
        # capturado ANTES de `rec` virar cumulativo abaixo.
        delta_ev = usage_event_from_record(user.id, chat_id, message_id, rec)
        arts_changed: list[str] = []
        async with SessionLocal() as s:
            if arts_on:
                content, arts_changed = await artifacts_service.extract_and_apply(
                    s, chat_id, user.id, content
                )
            target = await s.get(Message, message_id)
            if target is not None:
                if delta_ev is not None:
                    s.add(delta_ev)
                sep = "" if target.content.endswith(("\n", " ")) else " "
                target.content = f"{target.content}{sep}{content}"
                old = target.usage or {}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"):
                    rec[k] = int(old.get(k, 0) or 0) + int(rec.get(k, 0) or 0)
                rec["cost"] = float(old.get("cost", 0.0) or 0.0) + rec["cost"]
                # soma os detalhamentos de entrada/saída
                for grp in ("input_breakdown", "output_breakdown"):
                    merged = dict(old.get(grp) or {})
                    for k, v in (rec.get(grp) or {}).items():
                        merged[k] = int(merged.get(k, 0) or 0) + int(v or 0)
                    if merged:
                        rec[grp] = merged
                target.tokens = rec["total_tokens"] or None
                target.cost = rec["cost"] or None
                target.usage = rec
                # anexa o raciocínio da continuação ao existente
                if reasoning:
                    old_r = target.reasoning or {}
                    target.reasoning = {
                        "text": (old_r.get("text", "") + "\n\n" + reasoning["text"]).strip(),
                        "seconds": round(
                            float(old_r.get("seconds", 0) or 0)
                            + float(reasoning.get("seconds", 0) or 0),
                            1,
                        ),
                    }
                # anexa novos usos de ferramenta aos já registrados
                if tools:
                    target.tool_events = (target.tool_events or []) + tools
                await s.commit()
        if arts_changed:
            await emit({"type": "artifacts", "ids": arts_changed})

    source = run_turn(
        api_key=api_key,
        model=model,
        history=history,
        user_text=user_text,
        chat_system_prompt=system_prompt,
        params=params,
        base_url=base_url,
        **(await _artifacts_kwargs(db, chat_id, user, arts_on, model_config)),
        session=TurnSession(
            user_id=user_id, user_tz=_session_tz(user, user_tz), chat_id=str(chat_id),
            agent_id=_mem_agent_id(model_config, model),
            user_profile=_user_profile_dict(user),
            codespace_project_id=str(chat.project_id) if chat.project_id else None,
        ),
        sift=sift,
        code_mode=_code_mode(model_config),
        skills=skills,
        use_context=_use_context(model_config),
        knowledge=_resolve_knowledge(chat, model_config, user),
        brain=await _brain_setup(db, user, chat, model_config),
        skill_learning=_skill_learning(model_config),
        realtime_datetime=_realtime_datetime(model_config),
        memory=_memory_opts(chat, model_config, user),
        media=await _media_opts(db, user, model_config),
    )
    gen = generation.start(str(chat_id), source, _finish)
    return _sse_stream(_subscribe(gen))


