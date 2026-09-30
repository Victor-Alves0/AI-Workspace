"""Subagentes em SEGUNDO PLANO: a IA solta o agente e segue (ou encerra o turno); quando
ele termina, o chat é acordado num turno novo com o relatório (mesmo caminho do wake
dos comandos longos do Codespace, `resume.resume_chat_turn`).

Vários agentes que terminam juntos (ou enquanto o chat ainda gera) são entregues num
wake só: uma fila de notas por chat e um único "acordador" por chat, que espera o chat
ficar ocioso, junta tudo o que chegou e dispara o turno.

Enquanto rodam, o progresso de cada agente (e de cada membro de uma equipe) fica aqui
(`snapshot`): o painel lateral lê por `GET /chats/{chat}/agents/jobs/{job}` e a IA
principal vê o placar no próximo turno (`status_block`). Ao terminar, o resultado
completo (membros, relatórios, passos) é gravado no próprio card da mensagem que soltou
o trabalho — o painel continua mostrando tudo depois de recarregar.

Durável (como o Claude Code guarda a transcrição de cada subagente para retomá-lo):
cada trabalho tem uma linha em `subagent_jobs` com a receita (`spec`) e o checkpoint de
cada membro que terminou. No boot, `recover()` retoma os interrompidos só com o que
faltava e entrega os que terminaram sem chegar a acordar o chat. O progresso AO VIVO
(passos de cada agente) fica só em memória — após um restart o membro recomeça a tarefa.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .. import bg

logger = logging.getLogger(__name__)

NOTE_PREFIX = "[Agente em segundo plano concluído]"
_IDLE_TIMEOUT_S = 6 * 3600   # o turno principal pode seguir trabalhando por muito tempo
_REPORT_CHARS = 12000        # teto do relatório na nota (o resto fica no card do agente)
_KEEP_DONE_S = 2 * 3600      # quanto tempo o painel ainda lê um trabalho que terminou
_TEXT_CAP = 4000             # texto/raciocínio ao vivo por item (guarda o fim)
_TIMELINE_CAP = 120          # itens ao vivo por agente

_MAX_ATTEMPTS = 3            # restarts seguidos que um trabalho aguenta antes de desistir

_jobs: dict[str, dict[str, Any]] = {}
_pending: dict[str, list[tuple[str | None, str]]] = {}   # chat → (job, nota do wake)
_patches: dict[str, list[tuple[str, dict]]] = {}   # chat → (job, resultado p/ o card)
_wakers: dict[str, asyncio.Task] = {}


def start(chat_id: str, name: str, task: str, run: Callable[[], Awaitable[dict]],
          members: list[dict] | None = None, *, jid: str | None = None,
          durable: bool = False, done: dict[int, dict] | None = None) -> str:
    """Solta o agente; devolve o id do trabalho. `run()` executa o agente inteiro.
    `members` (equipe): nome e tarefa de cada membro, para o progresso ao vivo.
    `durable`: o trabalho tem linha no banco (resultado gravado ao terminar);
    `done`: membros que já tinham terminado (retomada)."""
    jid = jid or uuid.uuid4().hex[:10]
    live: dict[str, Any] = {"timeline": []}
    if members is not None:
        live = {"members": [{"name": str(m.get("name") or f"Agente {i + 1}"),
                             "task": str(m.get("task") or "")[:300],
                             "state": "queued", "timeline": []}
                            for i, m in enumerate(members)],
                "synthesizing": False}
    for i, r in (done or {}).items():
        if members is not None and 0 <= i < len(live["members"]):
            live["members"][i]["state"] = "failed" if r.get("error") else "done"
    _jobs[jid] = {"chat_id": chat_id, "name": name, "task": task, "status": "running",
                  "started": time.time(), "live": live}

    async def _go() -> None:
        try:
            result = await run()
        except Exception as exc:  # noqa: BLE001 - o erro vira o relatório
            logger.warning("subagente em segundo plano falhou (%s): %s", name, exc)
            result = {"error": f"o subagente falhou: {exc}"}
        job = _jobs.get(jid)
        if job is not None:
            job["status"] = "failed" if isinstance(result, dict) and result.get("error") else "done"
            job["finished"] = time.time()
            for m in job["live"].get("members") or []:
                if m["state"] in ("queued", "running"):
                    m["state"] = "failed"
            job["live"]["synthesizing"] = False
        card = result.get("card") if isinstance(result, dict) else None
        nota = note_for(name, task, result)
        if durable:
            falhou = isinstance(result, dict) and bool(result.get("error"))
            await _db_finish(jid, "failed" if falhou else "done", nota,
                             card if isinstance(card, dict) else None)
        if isinstance(card, dict):
            _patches.setdefault(chat_id, []).append((jid, card))
        _deliver(chat_id, nota, jid if durable else None)

    bg.spawn(_go(), name=f"subagent-bg-{jid}")
    _gc()
    return jid


def _gc() -> None:
    agora = time.time()
    for jid in [j for j, v in _jobs.items()
                if v.get("finished") and agora - v["finished"] > _KEEP_DONE_S]:
        _jobs.pop(jid, None)


def _apply(live: dict, ev: dict) -> None:
    """Mesma regra do cliente (`applySubagentProgress`): um evento de progresso do agente
    vira item da linha do tempo (texto/raciocínio emendam no último do mesmo tipo)."""
    tl: list[dict] = live.setdefault("timeline", [])
    last = tl[-1] if tl else None

    def _emenda(kind: str, txt: str) -> None:
        if last is not None and last.get("kind") == kind:
            last["text"] = (last["text"] + txt)[-_TEXT_CAP:]
        else:
            tl.append({"kind": kind, "text": txt[-_TEXT_CAP:]})

    if isinstance(ev.get("user"), str):
        tl.append({"kind": "user", "text": ev["user"]})
    elif isinstance(ev.get("reasoning"), str):
        _emenda("reasoning", ev["reasoning"])
    elif isinstance(ev.get("text"), str):
        _emenda("text", ev["text"])
    elif isinstance(ev.get("result"), str):
        for it in reversed(tl):
            if it.get("kind") == "tool" and it.get("tool") == ev["result"] and it.get("ok") is None:
                it["ok"] = ev.get("ok") is not False
                if isinstance(ev.get("preview"), str):
                    it["preview"] = ev["preview"]
                break
    elif isinstance(ev.get("tool"), str):
        item = {"kind": "tool", "tool": ev["tool"], "ok": None}
        for k in ("detail", "call"):
            if isinstance(ev.get(k), str):
                item[k] = ev[k]
        if isinstance(ev.get("args"), dict):
            item["args"] = ev["args"]
        tl.append(item)
    if len(tl) > _TIMELINE_CAP:
        del tl[: len(tl) - _TIMELINE_CAP]


def progress(jid: str | None, ev: dict) -> None:
    """Evento de progresso do trabalho `jid`: de um agente só, ou de uma equipe
    ({"member": i, "status": "running"|"progress"|"done"} / {"status": "synthesis"})."""
    job = _jobs.get(jid or "")
    if job is None:
        return
    live = job["live"]
    membros = live.get("members")
    if membros is None:
        if ev.get("state") != "running":
            _apply(live, ev)
        return
    if ev.get("status") == "synthesis":
        live["synthesizing"] = True
        return
    i = ev.get("member")
    if not isinstance(i, int) or not 0 <= i < len(membros):
        return
    m = membros[i]
    st = ev.get("status")
    if st == "running":
        m["state"] = "running"
    elif st == "done":
        m["state"] = "done" if ev.get("ok", True) else "failed"
    elif st == "progress":
        if m["state"] == "queued":
            m["state"] = "running"
        _apply(m, {k: v for k, v in ev.items() if k not in ("member", "status")})


def snapshot(chat_id: str, jid: str) -> dict | None:
    """Estado do trabalho p/ o painel; None se não existe (ou é de outro chat)."""
    job = _jobs.get(jid)
    if job is None or job["chat_id"] != chat_id:
        return None
    return {"status": job["status"], "name": job["name"], "started": job["started"],
            **job["live"]}


def status_block(chat_id: str) -> str:
    """Placar dos trabalhos em segundo plano AINDA rodando neste chat, p/ o turno
    principal responder "como está?" com o que é verdade (e não inventar)."""
    linhas = []
    for job in _jobs.values():
        if job["chat_id"] != chat_id or job["status"] != "running":
            continue
        mins = int((time.time() - job["started"]) // 60)
        membros = job["live"].get("members")
        if membros is not None:
            feitos = sum(1 for m in membros if m["state"] in ("done", "failed"))
            rodando = sum(1 for m in membros if m["state"] == "running")
            extra = "; merging the reports" if job["live"].get("synthesizing") else ""
            linhas.append(f"- team \"{job['name']}\": {feitos}/{len(membros)} agents finished, "
                          f"{rodando} working, running for {mins} min{extra}")
        else:
            linhas.append(f"- agent \"{job['name']}\": working, running for {mins} min")
    if not linhas:
        return ""
    return ("Background work still running in this chat (live status; the report arrives "
            "by itself in a new turn when it finishes):\n" + "\n".join(linhas))


def note_for(name: str, task: str, result: Any) -> str:
    """A nota (mensagem de usuário do wake) com o relatório do agente."""
    if isinstance(result, dict) and result.get("error"):
        corpo = f"Falhou: {result['error']}"
    else:
        saida = str((result or {}).get("output") if isinstance(result, dict) else result or "")
        corpo = saida[:_REPORT_CHARS] + ("\n[relatório cortado]" if len(saida) > _REPORT_CHARS else "")
        if isinstance(result, dict) and result.get("note"):
            corpo += f"\n\n{result['note']}"
    return (f"{NOTE_PREFIX} {name}\n\nTarefa: {task}\n\nRelatório:\n{corpo}\n\n"
            "Continue a partir deste resultado.")


def _deliver(chat_id: str, note: str, jid: str | None = None) -> None:
    _pending.setdefault(chat_id, []).append((jid, note))
    waker = _wakers.get(chat_id)
    if waker is None or waker.done():
        _wakers[chat_id] = bg.spawn(_waker(chat_id), name=f"subagent-wake-{chat_id}")


async def _persist_cards(chat_id: str) -> None:
    """Grava o resultado final no card que soltou o trabalho (as DUAS cópias do evento:
    `tool_events` e `reasoning.steps`). O card passa de "em segundo plano" para o
    resultado completo — membros, relatórios e passos continuam no painel."""
    fila = _patches.pop(chat_id, [])
    if not fila:
        return
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from ..db import SessionLocal
    from ..models import Message

    por_job = dict(fila)
    try:
        cid = uuid.UUID(chat_id)
    except ValueError:
        return
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(Message).where(Message.chat_id == cid, Message.role == "assistant",
                                  Message.tool_events.is_not(None))
            .order_by(Message.created_at.desc()).limit(200)
        )
        for msg in rows:
            mudou = False

            def _troca(ev: Any) -> bool:
                if not isinstance(ev, dict) or ev.get("kind") != "result":
                    return False
                data = ev.get("data")
                if not isinstance(data, dict) or data.get("job_id") not in por_job:
                    return False
                ev["data"] = {**data, **por_job[data["job_id"]], "job_id": data["job_id"],
                              "background": True}
                return True

            evs = list(msg.tool_events or [])
            for e in evs:
                mudou |= _troca(e)
            if not mudou:
                continue
            msg.tool_events = evs
            flag_modified(msg, "tool_events")
            if isinstance(msg.reasoning, dict):
                rz = dict(msg.reasoning)
                for st in rz.get("steps") or []:
                    _troca(st.get("event") if isinstance(st, dict) else None)
                msg.reasoning = rz
                flag_modified(msg, "reasoning")
        await db.commit()


async def _waker(chat_id: str) -> None:
    from . import generation, resume  # lazy: resume importa meio chat

    try:
        while _pending.get(chat_id):
            fim = time.monotonic() + _IDLE_TIMEOUT_S
            while (g := generation.get_active(chat_id)) is not None and not g.done:
                if time.monotonic() > fim:
                    logger.warning("wake de subagente descartado: chat %s nunca ficou ocioso", chat_id)
                    _pending.pop(chat_id, None)
                    return
                await asyncio.sleep(1.0)
            fila = _pending.pop(chat_id, [])
            if not fila:
                break
            notas = [n for _, n in fila]
            # o turno que soltou o trabalho já foi gravado (o chat está ocioso)
            try:
                await _persist_cards(chat_id)
            except Exception:  # noqa: BLE001 - o card é bônus; o wake segue
                logger.exception("não gravei o resultado do agente no card (chat %s)", chat_id)
            nomes = [n[len(NOTE_PREFIX):].split("\n", 1)[0].strip() for n in notas]
            await resume.resume_chat_turn(
                chat_id, "\n\n---\n\n".join(notas),
                notify_title="Agente em segundo plano concluído" if len(notas) == 1
                else f"{len(notas)} agentes em segundo plano concluídos",
                notify_body=", ".join(nomes)[:200],
            )
            await _db_delivered([j for j, _ in fila if j])
            await asyncio.sleep(1.0)  # a geração do wake se registra antes da próxima volta
    except Exception:  # noqa: BLE001 - wake é best-effort, nunca derruba o processo
        logger.exception("wake de subagentes falhou (chat %s)", chat_id)
    finally:
        _wakers.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Durabilidade: registro no banco, checkpoint por membro, retomada no boot
# ---------------------------------------------------------------------------

def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


def start_spec(chat_id: str, user_id: Any, name: str, task: str, spec: dict,
               members: list[dict] | None = None) -> str:
    """Solta um trabalho descrito por uma receita (`turn_setup.run_background_spec`) e o
    registra no banco antes de rodar — é o que permite retomá-lo após um restart."""
    jid = uuid.uuid4().hex[:10]
    spec = _jsonable(spec)

    async def _run() -> dict:
        from .turn_setup import run_background_spec

        await _db_insert(jid, chat_id, user_id, name, task, spec)
        return await run_background_spec(jid, uuid.UUID(str(user_id)), uuid.UUID(chat_id), spec)

    return start(chat_id, name, task, _run, members=members, jid=jid, durable=True)


def checkpoint(jid: str, i: int, result: dict) -> None:
    """Membro `i` terminou: grava o resultado dele (um restart não o roda de novo)."""
    bg.spawn(_db_checkpoint(jid, i, _jsonable(result)), name=f"subagent-ckpt-{jid}")


async def _db_insert(jid: str, chat_id: str, user_id: Any, name: str, task: str, spec: dict) -> None:
    from ..db import SessionLocal
    from ..models import SubagentJob

    try:
        async with SessionLocal() as db:
            db.add(SubagentJob(job_key=jid, chat_id=uuid.UUID(chat_id), user_id=uuid.UUID(str(user_id)),
                               kind=str(spec.get("kind") or "agent"), name=name[:200], task=task,
                               spec=spec, results={}))
            await db.commit()
    except Exception:  # noqa: BLE001 - sem o registro o trabalho roda igual (só não é retomável)
        logger.exception("não registrei o trabalho em segundo plano %s", jid)


async def _db_checkpoint(jid: str, i: int, result: dict) -> None:
    from sqlalchemy import text

    from ..db import SessionLocal

    try:
        async with SessionLocal() as db:
            await db.execute(text(
                "UPDATE subagent_jobs SET results = results || jsonb_build_object(CAST(:k AS text), "
                "CAST(:v AS jsonb)), updated_at = now() WHERE job_key = :j"),
                {"k": str(i), "v": json.dumps(result), "j": jid})
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("checkpoint do trabalho %s (membro %d) falhou", jid, i)


async def _db_finish(jid: str, status: str, note: str, card: dict | None) -> None:
    from sqlalchemy import update

    from ..db import SessionLocal
    from ..models import SubagentJob

    try:
        async with SessionLocal() as db:
            await db.execute(update(SubagentJob).where(SubagentJob.job_key == jid).values(
                status=status, note=note, card=_jsonable(card) if card is not None else None))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("não gravei o fim do trabalho %s", jid)


async def _db_delivered(jids: list[str]) -> None:
    if not jids:
        return
    from sqlalchemy import update

    from ..db import SessionLocal
    from ..models import SubagentJob

    try:
        async with SessionLocal() as db:
            await db.execute(update(SubagentJob).where(SubagentJob.job_key.in_(jids)).values(delivered=True))
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("não marquei a entrega de %s", jids)


async def recover() -> None:
    """BOOT: retoma o que um restart interrompeu. Trabalho rodando que não está neste
    processo → roda de novo só com os membros sem checkpoint (até _MAX_ATTEMPTS
    restarts; depois avisa o chat que falhou). Terminado mas não entregue → entrega
    (card + wake). Nunca levanta."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete, select

    from ..db import SessionLocal
    from ..models import SubagentJob

    try:
        async with SessionLocal() as db:
            await db.execute(delete(SubagentJob).where(
                SubagentJob.delivered.is_(True),
                SubagentJob.updated_at < datetime.now(timezone.utc) - timedelta(days=7)))
            rows = list(await db.scalars(select(SubagentJob).where(SubagentJob.delivered.is_(False))))
            retomar: list[SubagentJob] = []
            for j in rows:
                if j.job_key in _jobs:
                    continue
                if j.status == "running":
                    if j.attempts >= _MAX_ATTEMPTS:
                        j.status = "failed"
                        j.note = note_for(j.name, j.task, {"error": (
                            f"interrompido {j.attempts} vezes por reinícios do servidor; "
                            "não foi retomado de novo")})
                    else:
                        j.attempts += 1
                        retomar.append(j)
            await db.commit()
            pendentes = [(str(j.chat_id), j.job_key, j.note, j.card) for j in rows
                         if j.job_key not in _jobs and j.status in ("done", "failed")]
            relancar = [(str(j.chat_id), j.user_id, j.job_key, j.name, j.task, dict(j.spec or {}),
                         dict(j.results or {})) for j in retomar]
    except Exception:  # noqa: BLE001
        logger.exception("recuperação dos agentes em segundo plano falhou")
        return

    for chat_id, jid, nota, card in pendentes:
        if isinstance(card, dict):
            _patches.setdefault(chat_id, []).append((jid, card))
        if nota:
            _deliver(chat_id, nota, jid)
    for chat_id, user_id, jid, name, task, spec, results in relancar:
        done = {int(k): v for k, v in results.items() if str(k).isdigit() and isinstance(v, dict)}
        membros = (spec.get("team") or {}).get("members") if spec.get("kind") == "team" else None

        async def _run(jid=jid, user_id=user_id, chat_id=chat_id, spec=spec, done=done) -> dict:
            from .turn_setup import run_background_spec

            return await run_background_spec(jid, user_id, uuid.UUID(chat_id), spec, done=done)

        start(chat_id, name, task, _run, members=membros, jid=jid, durable=True, done=done)
    if pendentes or relancar:
        logger.warning("agentes em segundo plano: %d retomado(s), %d entregue(s) após restart",
                       len(relancar), len(pendentes))
