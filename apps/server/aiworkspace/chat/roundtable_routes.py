"""Rotas da MESA-REDONDA (multi-model chat): modelos conversam entre si, o
usuário guia/pausa. Montado sob o router /chats (ver routes.py)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_approved
from ..db import SessionLocal, get_db
from ..models import Message, ModelConfig, User
from ..usage_service import usage_event_from_record
from .orchestrator import TurnSession, run_turn
from .turn_setup import (
    _get_model_config,
    _get_owned_chat,
    _resolve_provider,
    _sse,
    _sse_stream,
    _tz_from_header,
    _usage_record,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Mesa-redonda (multi-model chat): modelos conversam entre si, o usuário guia.
# --------------------------------------------------------------------------- #
# chats com uma parada cooperativa pendente (o loop para após o turno atual).
_roundtable_stop: set[str] = set()


class RoundtableRunIn(BaseModel):
    content: str = ""           # injeção do usuário antes de rodar (opcional)
    steps: str = "auto"         # "one" (um turno) | "auto" (várias rodadas)
    next: str | None = None     # id do participante que deve falar (modo manual)


def _rt_speaker(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name") or "Modelo",
        "model": p.get("model"),
        "color": p.get("color"),
    }


def _rt_system(mc: ModelConfig | None, p: dict, names: list[str], self_name: str) -> str:
    frame = (
        f"Você participa de uma conversa em grupo (mesa-redonda) com: {', '.join(names)}. "
        f"Você é {self_name}. Contribua de forma concisa e natural, avançando a discussão. "
        "NÃO escreva as falas dos outros nem prefixe seu próprio nome; mensagens marcadas "
        "com 'Nome:' são dos outros participantes."
    )
    parts = [mc.system_prompt if mc else None, p.get("persona"), frame]
    return "\n\n".join([x for x in parts if x])


def _rt_context(convo: list[dict], names: dict[str, str], target_pid: str) -> tuple[list[dict], str]:
    """Mapeia o transcript compartilhado para a visão de um participante:
    falas próprias = assistant; dos outros = user "Nome: ..."; humano = user."""
    mapped: list[dict] = []
    for c in convo:
        if c["role"] == "user" or c.get("is_summary"):
            mapped.append({"role": "user", "content": c["content"]})
        elif c.get("speaker") == target_pid:
            mapped.append({"role": "assistant", "content": c["content"]})
        else:
            nm = names.get(c.get("speaker")) or "Participante"
            mapped.append({"role": "user", "content": f"{nm}: {c['content']}"})
    if not mapped:
        return [], "Inicie a conversa."
    return mapped[:-1], mapped[-1]["content"]


def _rt_next_rr(order: list[str], last: str | None) -> str:
    if last in order:
        return order[(order.index(last) + 1) % len(order)]
    return order[0]


async def _rt_moderator(mod: dict, resolved: list[dict], convo: list[dict], names: dict[str, str], user: User, user_tz: str) -> str | None:
    """Pergunta ao moderador (LLM) quem fala em seguida — devolve o id do
    participante, "STOP", ou None (fallback p/ round-robin)."""
    labels = [r["p"].get("name") or "Modelo" for r in resolved]
    transcript = "\n".join(
        f'{(names.get(c.get("speaker")) if c["role"] == "assistant" else "Usuário") or "Usuário"}: {c["content"]}'
        for c in convo
    ) or "(a conversa ainda não começou)"
    sysp = (
        "Você é o moderador de uma mesa-redonda. Participantes: " + ", ".join(labels) + ". "
        "Leia a conversa e responda APENAS com o nome do próximo participante que deve "
        "falar, ou 'STOP' se a conversa já cumpriu seu objetivo ou está repetitiva."
    )
    text = ""
    try:
        async for ev in run_turn(
            api_key=mod["api_key"], model=mod["model"],
            history=[{"role": "user", "content": transcript}],
            user_text="Quem deve falar agora? Responda só o nome, ou STOP.",
            chat_system_prompt=sysp, params={},
            session=TurnSession(user_id=str(user.id), user_tz=user_tz),
            base_url=mod["base_url"], use_tools=False, use_context=True,
        ):
            t = ev.get("type")
            if t == "token":
                text += ev.get("text", "")
            elif t == "done":
                text = ev.get("content") or text
    except Exception:  # noqa: BLE001 - moderador é best-effort
        return None
    ans = text.strip().lower()
    if "stop" in ans:
        return "STOP"
    for r in resolved:
        if (r["p"].get("name") or "").lower() and (r["p"]["name"].lower() in ans):
            return r["p"]["id"]
    return None


async def _rt_persist(chat_id: uuid.UUID, user: User, mc: ModelConfig | None, model: str, sp: dict, text: str, usage: dict | None, reasoning: dict | None) -> uuid.UUID:
    async with SessionLocal() as s:
        rec = _usage_record(usage, model, mc)
        m = Message(
            chat_id=chat_id, role="assistant", content=text or "", speaker=sp,
            reasoning=reasoning, tokens=rec["total_tokens"] or None,
            cost=rec["cost"] or None, usage=rec,
        )
        s.add(m)
        await s.flush()
        uev = usage_event_from_record(user.id, chat_id, m.id, rec)
        if uev is not None:
            s.add(uev)
        await s.commit()
        return m.id


@router.post("/{chat_id}/roundtable/run")
async def roundtable_run(
    chat_id: uuid.UUID,
    body: RoundtableRunIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    chat = await _get_owned_chat(db, chat_id, user)
    participants = list(chat.participants or [])
    if len(participants) < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adicione participantes à mesa-redonda")
    cfg = chat.roundtable_config or {}
    policy = cfg.get("turn_policy") or "round_robin"
    max_rounds = max(1, min(20, int(cfg.get("max_rounds") or 6)))
    explicit_next = body.next or cfg.get("next")

    # resolve cada participante (provedor + modelo custom + system) ANTES de streamar,
    # pois a `db` da request fecha ao retornar o StreamingResponse.
    resolved: list[dict] = []
    for p in participants:
        model = p.get("model") or ""
        if not model:
            continue
        mc = None
        if p.get("model_config_id"):
            try:
                mc = await _get_model_config(db, uuid.UUID(str(p["model_config_id"])), user)
            except (ValueError, TypeError):
                mc = None
        # Participantes customizados também são referências vivas: a mesa não
        # deve conservar o modelo-base que estava salvo quando ela foi criada.
        runtime_model = mc.base_model if mc is not None and mc.base_model else model
        try:
            api_key, base_url = await _resolve_provider(db, user, runtime_model)
        except HTTPException:
            continue
        runtime_participant = {
            **p,
            "model": runtime_model,
            # O nome é apresentação, mas também entra no contexto da mesa. Atualiza
            # junto com o preset para que reutilizar nomes não deixe a conversa com
            # um rótulo antigo.
            "name": mc.name if mc is not None else (p.get("name") or "Modelo"),
        }
        resolved.append({
            "p": runtime_participant, "mc": mc, "api_key": api_key, "base_url": base_url,
            "system": "",  # preenchido após todos os nomes atuais serem conhecidos
            "params": (mc.params if mc else {}) or {},
            "model": runtime_model,
        })
    if not resolved:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhum participante com provedor válido")

    current_names = [r["p"]["name"] for r in resolved]
    for r in resolved:
        r["system"] = _rt_system(r["mc"], r["p"], current_names, r["p"]["name"])

    mod = None
    if policy == "moderator":
        mm = (cfg.get("moderator") or {}).get("model")
        if mm:
            try:
                mkey, mbase = await _resolve_provider(db, user, mm)
                mod = {"model": mm, "api_key": mkey, "base_url": mbase}
            except HTTPException:
                mod = None

    # injeção do usuário (guia a conversa) — mensagem role=user, sem speaker
    if (body.content or "").strip():
        um = Message(chat_id=chat.id, role="user", content=body.content)
        db.add(um)
        if chat.title == "Novo Chat":
            chat.title = body.content[:60]
        await db.commit()

    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    convo: list[dict] = []
    for m in rows:
        if m.role in ("user", "assistant") and m.content and not m.compacted:
            convo.append({
                "role": m.role,
                "content": m.content,
                "speaker": (m.speaker or {}).get("id") if m.role == "assistant" else None,
                # o resumo da compactação entra como nota de contexto neutra, não como
                # fala de um "Participante" fantasma
                "is_summary": bool(m.is_summary),
            })
    last_pid = next((c["speaker"] for c in reversed(convo) if c["role"] == "assistant"), None)

    by_id = {r["p"]["id"]: r for r in resolved}
    order = [r["p"]["id"] for r in resolved]
    names_map = {r["p"]["id"]: (r["p"].get("name") or "Modelo") for r in resolved}
    cid = str(chat_id)

    async def _source():
        _roundtable_stop.discard(cid)
        lp = last_pid
        turns = 0
        cap = 1 if body.steps == "one" else max_rounds * max(1, len(resolved))
        try:
            while turns < cap:
                if cid in _roundtable_stop:
                    yield _sse({"type": "roundtable_paused"})
                    break
                pid = None
                if policy == "moderator" and mod is not None:
                    pid = await _rt_moderator(mod, resolved, convo, names_map, user, user_tz)
                    if pid == "STOP":
                        yield _sse({"type": "roundtable_done", "reason": "moderator"})
                        return
                if pid is None or pid not in by_id:
                    if turns == 0 and explicit_next in by_id:
                        pid = explicit_next
                    else:
                        pid = _rt_next_rr(order, lp)
                r = by_id[pid]
                sp = _rt_speaker(r["p"])
                sid = sp["id"]
                yield _sse({"type": "speaker_start", "speaker": sp})
                history, user_text = _rt_context(convo, names_map, sid)
                text = ""
                usage = None
                reasoning_obj = None
                try:
                    async for ev in run_turn(
                        api_key=r["api_key"], model=r["model"], history=history,
                        user_text=user_text, chat_system_prompt=r["system"], params=r["params"],
                        session=TurnSession(user_id=str(user.id), user_tz=user_tz, chat_id=cid),
                        base_url=r["base_url"], use_tools=False, use_context=True,
                    ):
                        t = ev.get("type")
                        if t == "token":
                            text += ev.get("text", "")
                            yield _sse({"type": "token", "text": ev.get("text", ""), "speaker": sid})
                        elif t == "reasoning":
                            yield _sse({"type": "reasoning", "text": ev.get("text", ""), "speaker": sid})
                        elif t == "done":
                            text = ev.get("content") or text
                            usage = ev.get("usage")
                            reasoning_obj = ev.get("reasoning")
                        elif t == "error":
                            yield _sse({"type": "error", "message": ev.get("message", "")})
                except Exception as exc:  # noqa: BLE001 - erro de um turno não derruba a mesa
                    logger.warning("Turno da mesa-redonda falhou: %s", exc)
                    yield _sse({"type": "error", "message": str(exc)})
                lp = sid
                turns += 1
                if text.strip():
                    mid = await _rt_persist(chat_id, user, r["mc"], r["model"], sp, text, usage, reasoning_obj)
                    convo.append({"role": "assistant", "content": text, "speaker": sid})
                    yield _sse({"type": "speaker_end", "speaker": sp, "message_id": str(mid)})
                else:
                    # turno vazio (erro/sem saída): NÃO persiste nem entra no histórico
                    # — uma mensagem de conteúdo vazio quebraria a próxima rodada em
                    # provedores que rejeitam mensagens vazias no contexto.
                    yield _sse({"type": "speaker_end", "speaker": sp, "message_id": None})
                if body.steps == "one":
                    break
        finally:
            _roundtable_stop.discard(cid)
        yield _sse({"type": "roundtable_done"})

    return _sse_stream(_source())


@router.post("/{chat_id}/roundtable/stop")
async def roundtable_stop(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_chat(db, chat_id, user)
    _roundtable_stop.add(str(chat_id))
    return {"ok": True}
