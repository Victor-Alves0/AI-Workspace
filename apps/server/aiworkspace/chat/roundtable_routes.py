"""Rotas da MESA-REDONDA: vários modelos trabalham em equipe no pedido do usuário.
Montado sob o router /chats (ver routes.py).

Cada fala é um turno de AGENTE completo — mesma preparação de um turno normal
(tools/SIFT com o projeto do Codespace ou o espaço de trabalho do chat, skills,
conhecimento, cérebro, memória, raciocínio, guardas, artefatos). A mesa roda como
uma geração em background (generation.start): sobrevive a F5, o "Parar" do
compositor interrompe na hora (o parcial é salvo) e uma mensagem do usuário no meio
entra como nova instrução antes da próxima fala. A lógica pura está em roundtable.py.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import budget_service, uploads_service
from ..auth.deps import require_approved
from ..db import SessionLocal, get_db
from ..models import Chat, Message, User
from ..schemas.chat import Attachment
from ..tools.loader import get_sift_for_user
from ..usage_service import usage_event_from_record
from . import artifacts as artifacts_service
from . import attachment_context, generation
from . import roundtable as rt
from .orchestrator import TurnSession, run_turn, run_turn_guarded
from .turn_setup import (
    _artifacts_enabled,
    _artifacts_kwargs,
    _brain_setup,
    _code_mode,
    _get_model_config,
    _get_owned_chat,
    _load_skills,
    _media_opts,
    _mem_agent_id,
    _memory_opts,
    _params_with_chat_reasoning,
    _prepare_attachments,
    _realtime_datetime,
    _remember_tz,
    _resolve_guards,
    _resolve_knowledge,
    _resolve_provider,
    _session_tz,
    _skill_learning,
    _sse_stream,
    _subscribe,
    _tz_from_header,
    _usage_record,
    _user_profile_dict,
    _workspace_on,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class RoundtableRunIn(BaseModel):
    content: str = Field(default="", max_length=100_000)  # instrução do usuário (opcional)
    steps: str = "auto"         # "one" (um turno) | "auto" (até concluir/pausar/teto)
    next: str | None = None     # id do participante que deve agir primeiro (modo manual)
    attachments: list[Attachment] = Field(default_factory=list, max_length=50)
    skill_ids: list[uuid.UUID] = Field(default_factory=list)


async def _moderator_pick(
    mod: dict, parts: list[dict], convo: list[dict], names: dict, user_id: str, user_tz: str,
) -> str | None:
    """Pergunta ao moderador (LLM) quem age agora → id, "STOP" ou None (round-robin)."""
    system, transcript = rt.moderator_prompt(parts, convo, names)
    text = ""
    try:
        async for ev in run_turn(
            api_key=mod["api_key"], model=mod["model"],
            history=[{"role": "user", "content": transcript}],
            user_text="Quem deve agir agora? Responda só o número, ou FIM.",
            chat_system_prompt=system, params={},
            session=TurnSession(user_id=user_id, user_tz=user_tz),
            base_url=mod["base_url"], use_tools=False, use_context=True,
            realtime_datetime=False,
        ):
            t = ev.get("type")
            if t == "token":
                text += ev.get("text", "")
            elif t == "done":
                text = ev.get("content") or text
    except Exception:  # noqa: BLE001 - moderador é best-effort
        logger.warning("Moderador da mesa-redonda falhou", exc_info=True)
        return None
    return rt.parse_moderator(text, parts)


async def _load_convo(db: AsyncSession, chat_id: uuid.UUID) -> list[dict]:
    """Transcript da mesa: texto + quem falou + anexos/raciocínio (para o replay)."""
    rows = list(await db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.role.in_(("user", "assistant")),
               Message.compacted.is_(False))
        .order_by(Message.created_at)
    ))
    kept = [m for m in rows if attachment_context.keep(m)]
    entries = await attachment_context.history(kept)
    convo: list[dict] = []
    for m, e in zip(kept, entries):
        convo.append({
            "role": m.role,
            "content": e.get("content") or "",
            "speaker": (m.speaker or {}).get("id") if m.role == "assistant" else None,
            "speaker_name": (m.speaker or {}).get("name") if m.role == "assistant" else None,
            # o resumo da compactação entra como nota neutra, não como fala de alguém
            "is_summary": bool(m.is_summary),
            "tools": rt.tool_names(m.tool_events) if m.role == "assistant" else [],
            "entry": e,
        })
    return convo


@router.post("/{chat_id}/roundtable/run")
async def roundtable_run(
    chat_id: uuid.UUID,
    body: RoundtableRunIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    chat = await _get_owned_chat(db, chat_id, user)
    cid = str(chat_id)
    content = (body.content or "").strip()

    # Mesa já rodando: a mensagem vira instrução para a PRÓXIMA fala (a atual segue);
    # sem mensagem, só reassina o stream em curso.
    active = generation.get_active(cid)
    if active is not None and not active.done:
        if not content:
            return _sse_stream(_subscribe(active), trace_id=active.trace_id)
        atts = await _prepare_attachments([a.model_dump() for a in body.attachments], None)
        await uploads_service.bind(db, user.id, chat.id, atts)
        db.add(Message(chat_id=chat.id, role="user", content=body.content,
                       attachments=uploads_service.persistable(atts) or None))
        await db.commit()
        await active.enqueue(body.content, steer=True)
        return {"queued": True}

    participants = list(chat.participants or [])
    if not participants:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adicione participantes à mesa-redonda")
    await budget_service.enforce_or_raise(db, user)
    _remember_tz(user, user_tz)
    cfg = chat.roundtable_config or {}
    policy = cfg.get("turn_policy") or "round_robin"
    max_rounds = max(1, min(20, int(cfg.get("max_rounds") or 6)))
    explicit_next = body.next or (cfg.get("next") if policy == "manual" else None)

    # valida cada participante (preset vivo + provedor) antes de abrir o stream; a
    # preparação completa do turno é refeita a cada fala (artefatos/preset atuais).
    resolved: list[dict] = []
    for p in participants:
        mc = None
        if p.get("model_config_id"):
            mc = await _get_model_config(db, p["model_config_id"], user)
        # participante customizado é referência viva: vale o modelo-base ATUAL do preset
        model = mc.base_model if mc is not None and mc.base_model else (p.get("model") or "")
        if not model:
            continue
        try:
            await _resolve_provider(db, user, model)
        except HTTPException:
            continue
        resolved.append({
            "p": {**p, "model": model, "name": mc.name if mc is not None else (p.get("name") or "Modelo")},
            "mc_id": mc.id if mc is not None else None,
        })
    if not resolved:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhum participante com provedor válido")

    mod = None
    if policy == "moderator":
        mcfg = cfg.get("moderator") or {}
        mm = mcfg.get("model")
        if mcfg.get("model_config_id"):
            mmc = await _get_model_config(db, mcfg["model_config_id"], user)
            if mmc is not None and mmc.base_model:
                mm = mmc.base_model
        if mm:
            try:
                mkey, mbase = await _resolve_provider(db, user, mm)
                mod = {"model": mm, "api_key": mkey, "base_url": mbase}
            except HTTPException:
                mod = None

    # a instrução do usuário (com anexos) é a mensagem que a equipe vai executar
    if content or body.attachments:
        atts = await _prepare_attachments([a.model_dump() for a in body.attachments], None)
        await uploads_service.bind(db, user.id, chat.id, atts)
        db.add(Message(chat_id=chat.id, role="user", content=body.content,
                       attachments=uploads_service.persistable(atts) or None))
        if chat.title == "Novo Chat":
            chat.title = (body.content[:60] or "Anexo")
        await db.commit()

    convo = await _load_convo(db, chat_id)
    if not any(c["role"] == "user" and not c.get("is_summary") for c in convo):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Diga à mesa o que fazer")
    last_pid = next((c["speaker"] for c in reversed(convo) if c["role"] == "assistant"), None)

    speakers = {r["p"]["id"]: rt.speaker_of(r["p"]) for r in resolved}
    by_id = {r["p"]["id"]: r for r in resolved}
    order = [r["p"]["id"] for r in resolved]
    names: dict[str | None, str] = {r["p"]["id"]: r["p"]["name"] for r in resolved}
    # falas de participantes que já saíram da mesa continuam rotuladas pelo nome salvo
    for c in convo:
        sp = c.get("speaker")
        if sp and sp not in names:
            names[sp] = c.get("speaker_name") or "Participante"
    user_id = user.id
    extra_skills = list(body.skill_ids)
    # runtime da última preparação de cada participante (usado ao persistir)
    runtime: dict[str, dict[str, Any]] = {}

    async def turn(pid: str, history: list[dict], user_text: str):
        r = by_id[pid]
        async with SessionLocal() as s:
            u = await s.get(User, user_id)
            ch = await s.get(Chat, chat_id)
            # orçamento checado a cada fala: uma mesa longa não passa do teto
            await budget_service.enforce_or_raise(s, u)
            mc = await _get_model_config(s, r["mc_id"], u) if r["mc_id"] else None
            model = mc.base_model if mc is not None and mc.base_model else r["p"]["model"]
            api_key, base_url = await _resolve_provider(s, u, model)
            project = str(ch.project_id) if ch.project_id else None
            others = [names[o] for o in order if o != pid]
            arts_on = _artifacts_enabled(u)
            runtime[pid] = {"model": model, "mc": mc, "arts_on": arts_on}
            kwargs = dict(
                guards=await _resolve_guards(s, u, mc),
                api_key=api_key, model=model, base_url=base_url,
                history=history, user_text=user_text,
                chat_system_prompt=rt.build_system(
                    mc.system_prompt if mc is not None else None,
                    r["p"].get("persona"), names[pid], others,
                ),
                params=_params_with_chat_reasoning(mc.params if mc is not None else {}, ch.params),
                **(await _artifacts_kwargs(s, chat_id, u, arts_on, mc)),
                session=TurnSession(
                    user_id=str(user_id), user_tz=_session_tz(u, user_tz), chat_id=cid,
                    agent_id=_mem_agent_id(mc, model), user_profile=_user_profile_dict(u),
                    codespace_project_id=project,
                ),
                sift=await get_sift_for_user(
                    s, u.id, mc, codespace_project_id=project, workspace=_workspace_on(ch, mc),
                ),
                code_mode=_code_mode(mc),
                skills=await _load_skills(s, u, mc, extra_skills),
                # a mesa É o contexto compartilhado: sem histórico o agente não sabe o pedido
                use_context=True,
                knowledge=_resolve_knowledge(ch, mc, u),
                brain=await _brain_setup(s, u, ch, mc),
                skill_learning=_skill_learning(mc),
                realtime_datetime=_realtime_datetime(mc),
                memory=_memory_opts(ch, mc, u),
                media=await _media_opts(s, u, mc),
            )
        async for ev in run_turn_guarded(**kwargs):
            yield ev

    async def persist(pid: str, sp: dict, text: str, reasoning: dict | None, col: dict) -> dict | None:
        info = runtime.get(pid) or {"model": by_id[pid]["p"]["model"], "mc": None, "arts_on": False}
        rec = _usage_record(col.get("usage"), info["model"], info["mc"])
        changed: list[str] = []
        async with SessionLocal() as s:
            if info["arts_on"] and text:
                text, changed = await artifacts_service.extract_and_apply(s, chat_id, user_id, text)
            m = Message(
                chat_id=chat_id, role="assistant", content=text or "", speaker=sp,
                reasoning=reasoning, tokens=rec["total_tokens"] or None,
                cost=rec["cost"] or None, usage=rec, tool_events=col.get("tools"),
                memories_used=col.get("memories"),
            )
            s.add(m)
            await s.flush()
            uev = usage_event_from_record(user_id, chat_id, m.id, rec)
            if uev is not None:
                s.add(uev)
            await s.commit()
            mid = str(m.id)
        extra = [{"type": "artifacts", "ids": changed}] if changed else []
        return {"message_id": mid, "content": text, "extra": extra}

    parts = [{"id": r["p"]["id"], "name": r["p"]["name"], "role": r["p"].get("persona")} for r in resolved]

    async def pick(cv: list[dict]) -> str | None:
        return await _moderator_pick(mod, parts, cv, names, str(user_id), user_tz)

    genbox: dict[str, Any] = {}

    def drain_user() -> list[str]:
        g = genbox.get("gen")
        return (g.drain_steer() + g.drain_queue()) if g is not None else []

    cap = max_rounds * len(resolved)

    async def source():
        async for ev in rt.run_loop(
            order=order, speakers=speakers, names=names, convo=convo,
            turn=turn, persist=persist, policy=policy, cap=cap,
            one_step=body.steps == "one", explicit_next=explicit_next,
            last_pid=last_pid, pick=pick if mod is not None else None,
            drain_user=drain_user,
        ):
            yield ev

    async def _on_finish(_collected: dict, _emit) -> None:
        return None  # cada fala já é persistida no laço (inclusive o parcial ao parar)

    gen = generation.start(
        cid, source(), _on_finish, trace_user_id=str(user_id),
        trace_attrs={"turn_kind": "roundtable", "participants": len(resolved), "policy": policy},
    )
    genbox["gen"] = gen
    return _sse_stream(_subscribe(gen), trace_id=gen.trace_id)


@router.post("/{chat_id}/roundtable/stop")
async def roundtable_stop(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Pausa a mesa agora: interrompe a fala em curso (o parcial é salvo)."""
    await _get_owned_chat(db, chat_id, user)
    gen = generation.get_active(str(chat_id))
    return {"ok": True, "stopped": bool(gen and gen.stop())}
