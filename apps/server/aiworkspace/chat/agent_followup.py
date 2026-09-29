"""Conversa direta com um agente pelo painel dele.

- Agente TRABALHANDO: a mensagem entra no loop dele (caixa de correio → steer).
- Agente que JÁ TERMINOU: vira uma continuação da conversa com ELE — mesma identidade
  (nome/instruções, ou o agente do usuário), histórico = tarefa + relatório + o que já
  foi conversado. Roda em segundo plano (sair do chat não perde a resposta) e fica
  gravada no próprio resultado do agente (`followups`), na mensagem do chat.
- A IA principal fica sabendo: cada troca deixa uma nota que entra no PRÓXIMO turno do
  chat (ver `notes_block`), para ela não seguir com o relatório antigo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from ..db import SessionLocal
from ..models import Chat, Message, ModelConfig, User
from . import agent_mailbox

logger = logging.getLogger(__name__)

_NOTES_KEY = "agent_notes:{chat}"
_NOTE_REPLY_CAP = 2000
_NOTES_TOTAL_CAP = 6000
_running: dict[str, asyncio.Task] = {}   # continuações em andamento (por chave da caixa)
_listeners: dict[str, list[asyncio.Queue]] = {}


class FollowupError(Exception):
    pass


def split_ref(ref: str) -> tuple[str, int | None]:
    call_id, _, idx = ref.partition("#")
    return call_id, (int(idx) if idx.isdigit() else None)


async def locate(db, chat_id: uuid.UUID, ref: str) -> tuple[Message, dict, dict, dict | None]:
    """(mensagem, evento de resultado, argumentos da chamada, membro da equipe | None)."""
    call_id, member = split_ref(ref)
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id, Message.role == "assistant",
                              Message.tool_events.is_not(None))
        .order_by(Message.created_at.desc())
    )
    for msg in rows:
        evs = msg.tool_events or []
        res = next((e for e in evs if e.get("kind") == "result" and e.get("id") == call_id
                    and e.get("name") in ("delegate", "delegate_team")), None)
        if res is None:
            continue
        call = next((e for e in evs if e.get("kind") == "call" and e.get("id") == call_id), None)
        args = (call or {}).get("data") or {}
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        if member is not None:
            membros = data.get("members") or []
            if member >= len(membros):
                raise FollowupError("membro da equipe não encontrado")
            spec = ((args.get("members") or [])[member:member + 1] or [{}])[0]
            return msg, res, spec, membros[member]
        return msg, res, args, None
    raise FollowupError("agente não encontrado nesta conversa")


def _agent_view(res: dict, args: dict, member: dict | None) -> dict:
    """Nome, tarefa, relatório e conversa anterior do agente."""
    from .orchestrator import _output_with_needs, _task_with_context

    d = member if member is not None else (res.get("data") or {})
    # os dados entregues pelo orquestrador e o pedido do agente fazem parte da conversa
    task = _task_with_context(str(d.get("task") or args.get("task") or ""), str(d.get("context") or ""))
    output = str(d.get("output") or d.get("error") or "")
    if d.get("needs"):
        output = _output_with_needs(output, d.get("needs"))
    return {
        "name": str(d.get("agent") or args.get("name") or "Agente"),
        "task": task,
        "output": output,
        "followups": list(d.get("followups") or []),
        "adhoc": bool(d.get("adhoc")) or member is not None or str(args.get("agent") or "") == "new",
        "key": str(args.get("agent") or "new"),
        "instructions": str(args.get("instructions") or ""),
    }


def prior_history(view: dict) -> list[dict]:
    hist = [{"role": "user", "content": view["task"] or "(tarefa)"},
            {"role": "assistant", "content": view["output"] or "(sem relatório)"}]
    for f in view["followups"]:
        if f.get("role") in ("user", "assistant") and f.get("content"):
            hist.append({"role": f["role"], "content": str(f["content"])})
    return hist


async def load_chain(db, chat_id: uuid.UUID, ref: str) -> dict:
    """Agente gravado → identidade + histórico inteiro. Uma retomada pelo orquestrador
    (`continue_agent`) grava `continues` apontando a anterior: refaz a corrente, para o
    agente lembrar de tudo o que já fez. `tools` = escopo efetivo (None = o do orquestrador)."""
    historico: list[dict] = []
    ponta: tuple[dict, dict] | None = None
    atual = ref
    for _ in range(8):
        _msg, res, args, member = await locate(db, chat_id, atual)
        view = _agent_view(res, args, member)
        d = member if member is not None else (res.get("data") or {})
        historico = prior_history(view) + historico
        if ponta is None:  # a identidade é a da ponta (a mais recente)
            ponta = view, d
        anterior = str(d.get("continues") or "")
        if not anterior or anterior == atual:
            break
        atual = anterior
    assert ponta is not None
    view, d = ponta
    tools = d.get("tools_granted") if isinstance(d.get("tools_granted"), list) else None
    return {"key": view["key"], "name": view["name"], "adhoc": view["adhoc"],
            "instructions": view["instructions"], "tools": tools, "history": historico}


# --------------------------------------------------------------------------- #
# notas para a IA principal (opção A)                                         #
# --------------------------------------------------------------------------- #
async def _add_note(db, chat_id: uuid.UUID, agent: str, user_text: str, reply: str) -> None:
    k = _NOTES_KEY.format(chat=chat_id)
    row = (await db.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": k})).scalar()
    notes = list(((row or {}).get("v") if isinstance(row, dict) else None) or [])
    notes.append({"agent": agent, "user": user_text[:1500], "reply": reply[:_NOTE_REPLY_CAP]})
    await db.execute(text(
        "INSERT INTO app_settings (id, key, value, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :k, CAST(:v AS jsonb), now(), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": k, "v": json.dumps({"v": notes[-10:]})})


async def take_notes_block(db, chat_id: uuid.UUID) -> str:
    """Bloco de sistema com o que o usuário conversou direto com os agentes desde o
    último turno — e consome as notas (entram uma vez só)."""
    k = _NOTES_KEY.format(chat=chat_id)
    row = (await db.execute(text("DELETE FROM app_settings WHERE key = :k RETURNING value"), {"k": k})).scalar()
    notes = ((row or {}).get("v") if isinstance(row, dict) else None) or []
    if not notes:
        return ""
    partes = []
    for n in notes:
        partes.append(f"### {n.get('agent')}\nUser: {n.get('user')}\n{n.get('agent')}: {n.get('reply')}")
    corpo = "\n\n".join(partes)
    if len(corpo) > _NOTES_TOTAL_CAP:
        corpo = "[…]\n" + corpo[-_NOTES_TOTAL_CAP:]
    return ("Since your last reply, the user talked DIRECTLY with some of your agents (from the "
            "agent's side panel). This may update or correct what those agents reported before — "
            "take it into account, and don't redo work they already did:\n\n" + corpo)


# --------------------------------------------------------------------------- #
# continuação                                                                 #
# --------------------------------------------------------------------------- #
def _emit(box: str, ev: dict) -> None:
    for q in list(_listeners.get(box, [])):
        q.put_nowait(ev)


async def _run(user_id: uuid.UUID, chat_id: uuid.UUID, ref: str, content: str, think: bool, box: str) -> None:
    from .turn_setup import _workspace_on, subagents_for_turn

    try:
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            chat = await db.get(Chat, chat_id)
            if user is None or chat is None or chat.user_id != user.id:
                raise FollowupError("conversa não encontrada")
            msg, res, args, member = await locate(db, chat_id, ref)
            view = _agent_view(res, args, member)
            chain = await load_chain(db, chat_id, ref)
            mc = await db.get(ModelConfig, chat.model_config_id) if chat.model_config_id else None
            opts = await subagents_for_turn(
                db, user, chat.id, str(chat.project_id) if chat.project_id else None, mc,
                workspace=_workspace_on(chat, mc))
            if opts is None or opts.run is None:
                raise FollowupError("os agentes estão desligados no modelo desta conversa")
            new = ({"name": view["name"], "instructions": view["instructions"] or (
                        "Continue helping the user with the task you already worked on.")}
                   if view["adhoc"] else None)
            if new is not None and chain["tools"] is not None:
                new["tools"] = chain["tools"]  # conversar não amplia o escopo concedido
            # esta continuação também recebe mensagens enquanto trabalha (mesma caixa)
            agent_mailbox.current_ref.set(ref)
            agent_mailbox.force_effort.set("high" if think else None)
            out = await opts.run(view["key"], content, new,
                                 progress=lambda ev: _emit(box, {"type": "progress", **ev}),
                                 prior=chain["history"])
            if out.get("error"):
                raise FollowupError(str(out["error"]))
            reply = str(out.get("output") or "")
            novos = [{"role": "user", "content": content},
                     {"role": "assistant", "content": reply, "timeline": out.get("timeline") or []}]
            # grava no resultado do agente, nas DUAS cópias da mensagem: `tool_events` e a
            # linha do tempo (`reasoning.steps`), que é de onde o painel lê ao reabrir
            await db.refresh(msg)
            call_id, idx = split_ref(ref)

            def _anexa(ev: dict | None) -> None:
                if not isinstance(ev, dict) or ev.get("kind") != "result" or ev.get("id") != call_id:
                    return
                data = ev.get("data") if isinstance(ev.get("data"), dict) else None
                if data is None:
                    return
                alvo = (data.get("members") or [])[idx] if idx is not None else data
                alvo["followups"] = list(alvo.get("followups") or []) + novos

            evs = list(msg.tool_events or [])
            for e in evs:
                _anexa(e)
            msg.tool_events = evs
            flag_modified(msg, "tool_events")
            if isinstance(msg.reasoning, dict):
                rz = dict(msg.reasoning)
                for st in rz.get("steps") or []:
                    _anexa(st.get("event") if isinstance(st, dict) else None)
                msg.reasoning = rz
                flag_modified(msg, "reasoning")
            await _add_note(db, chat_id, view["name"], content, reply)
            await db.commit()
            _emit(box, {"type": "done", "followups": novos})
    except FollowupError as exc:
        _emit(box, {"type": "error", "detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("continuação com o agente falhou")
        _emit(box, {"type": "error", "detail": f"falha: {exc}"})
    finally:
        _running.pop(box, None)


async def send(user: User, chat_id: uuid.UUID, ref: str, content: str,
               think: bool = False) -> asyncio.Queue | None:
    """Entrega ao agente. None = ele estava trabalhando e a mensagem entrou no loop
    dele; senão abre uma continuação e devolve a fila dos eventos dela (`listen`)."""
    box = agent_mailbox.key(str(chat_id), ref)
    assert box
    if agent_mailbox.send(box, content):
        return None
    if box in _running:
        raise FollowupError("o agente ainda está respondendo — espere ou mande de novo em instantes")
    # checa já, para o erro voltar na hora (e não pelo stream)
    async with SessionLocal() as db:
        await locate(db, chat_id, ref)
    # a fila entra ANTES da tarefa começar: nenhum evento se perde
    q: asyncio.Queue = asyncio.Queue()
    _listeners.setdefault(box, []).append(q)
    _running[box] = asyncio.get_running_loop().create_task(_run(user.id, chat_id, ref, content, think, box))
    return q


async def listen(chat_id: uuid.UUID, ref: str, q: asyncio.Queue) -> AsyncGenerator[dict, None]:
    box = agent_mailbox.key(str(chat_id), ref)
    assert box
    try:
        while True:
            if box not in _running and q.empty():
                return
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield {"type": "ping"}
                continue
            yield ev
            if ev.get("type") in ("done", "error"):
                return
    finally:
        lst = _listeners.get(box) or []
        if q in lst:
            lst.remove(q)
        if not lst:
            _listeners.pop(box, None)


def running(chat_id: uuid.UUID, ref: str) -> bool:
    box = agent_mailbox.key(str(chat_id), ref)
    return bool(box) and (box in _running or agent_mailbox.is_open(box))


__all__ = ["send", "listen", "running", "take_notes_block", "FollowupError"]
