"""Serviço do Ledger de tarefa (memória de trabalho por chat).

Duas faces: (1) funções ASSÍNCRONAS (load/render) usadas na montagem do turno; (2)
uma função SÍNCRONA `apply()` usada pela tool SIFT (roda no threadpool → engine efêmera
+ asyncio.run, como graph_service). Uma linha por operação — a tool só encaminha.

Rendering compacto p/ injeção no contexto: mostra objetivo, plano (com status), achados
(com ciclo de vida) e próximo passo, e traz a REGRA anti-ancoragem (achado 'refuted' não
volta como conclusão). Agnóstico de domínio (dev/refactor/security).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..db import SessionLocal
from ..models import TaskLedger

_NOTES_CAP = 40           # notas de evidência guardadas (append-only, capado)
_PLAN_CAP = 60
_FINDINGS_CAP = 60
_PLAN_MARK = {"todo": "[ ]", "doing": "[»]", "done": "[x]", "blocked": "[!]"}
_FIND_MARK = {"open": "?", "confirmed": "✓", "refuted": "✗"}
_PLAN_STATUSES = ("todo", "doing", "done", "blocked")
_FIND_STATUSES = ("open", "confirmed", "refuted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_dict(l: TaskLedger | None) -> dict | None:
    if l is None:
        return None
    return {
        "objective": l.objective or "", "status": l.status or "active",
        "plan": list(l.plan or []), "findings": list(l.findings or []),
        "notes": list(l.notes or []), "next_step": l.next_step or "",
    }


async def load(chat_id: str) -> dict | None:
    """Carrega o ledger do chat (dict) — usado na montagem do turno. Roda no loop do
    run_turn, então usa o pool GLOBAL (SessionLocal), sem engine efêmera por turno (isto
    é hot path: TODO turno de TODO chat passa aqui). `apply()` (tool, threadpool) é que
    precisa da engine efêmera — não pode tocar o SessionLocal amarrado ao outro loop."""
    try:
        cid = uuid.UUID(str(chat_id))
    except ValueError:
        return None
    async with SessionLocal() as db:
        l = (await db.scalars(select(TaskLedger).where(TaskLedger.chat_id == cid))).first()
        return _to_dict(l)


def render_block(led: dict | None) -> str:
    """Bloco compacto p/ o system prompt do turno. Vazio se não há objetivo nem plano."""
    if not led:
        return ""
    if not (led.get("objective") or led.get("plan") or led.get("findings") or led.get("next_step")):
        return ""
    lines: list[str] = [
        "## Ledger da tarefa — sua MEMÓRIA DE TRABALHO deste objetivo (persiste entre turnos)",
        "Mantenha-o ATUALIZADO com a tool `task.ledger.track` conforme trabalha: marque passos, "
        "registre evidência, e mova achados para confirmed/refuted. NÃO re-derive o que já "
        "está aqui. Um achado 'refuted' (✗) foi DERRUBADO pela evidência — NÃO o reporte de "
        "novo como risco/conclusão válida.",
    ]
    obj = led.get("objective") or "(sem objetivo definido — defina com task.ledger.track action=set)"
    lines.append(f"\nObjetivo [{led.get('status', 'active')}]: {obj}")
    plan = led.get("plan") or []
    if plan:
        lines.append("Plano:")
        for s in plan[:_PLAN_CAP]:
            mark = _PLAN_MARK.get(s.get("status", "todo"), "[ ]")
            note = f" — {s['note']}" if s.get("note") else ""
            lines.append(f"  {mark} [{s.get('id', '?')}] {s.get('text', '')}{note}")
    finds = led.get("findings") or []
    if finds:
        lines.append("Achados:")
        for f in finds[:_FINDINGS_CAP]:
            mark = _FIND_MARK.get(f.get("status", "open"), "?")
            ev = f" — {f['evidence']}" if f.get("evidence") else ""
            lines.append(f"  {mark} [{f.get('id', '?')}] {f.get('text', '')}{ev}")
    notes = led.get("notes") or []
    if notes:
        lines.append("Notas recentes:")
        for n in notes[-6:]:
            lines.append(f"  - {n.get('text', '')}")
    if led.get("next_step"):
        lines.append(f"Próximo passo: {led['next_step']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Mutação — face síncrona da tool (engine efêmera, como graph_service)
# --------------------------------------------------------------------------- #
async def _apply_async(user_id: str, chat_id: str, project_id: str | None,
                       action: str, kw: dict) -> dict:
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                cid, uid = uuid.UUID(str(chat_id)), uuid.UUID(str(user_id))
            except ValueError:
                return {"error": "chat/usuário inválido"}
            l = (await db.scalars(select(TaskLedger).where(TaskLedger.chat_id == cid))).first()
            if l is None:
                pid = None
                try:
                    pid = uuid.UUID(str(project_id)) if project_id else None
                except ValueError:
                    pid = None
                l = TaskLedger(chat_id=cid, user_id=uid, project_id=pid)
                db.add(l)

            def _next_id(items: list, prefix: str) -> str:
                n = 1
                existing = {i.get("id") for i in items}
                while f"{prefix}{n}" in existing:
                    n += 1
                return f"{prefix}{n}"

            if action == "get":
                pass
            elif action == "set":
                if "objective" in kw and kw["objective"] is not None:
                    l.objective = str(kw["objective"])[:2000]
                if "next_step" in kw and kw["next_step"] is not None:
                    l.next_step = str(kw["next_step"])[:2000]
                if kw.get("status") in ("active", "done", "paused"):
                    l.status = kw["status"]
            elif action == "plan_add":
                text = str(kw.get("text") or "").strip()
                if not text:
                    return {"error": "informe 'text' do passo"}
                plan = list(l.plan or [])
                sid = _next_id(plan, "s")
                plan.append({"id": sid, "text": text[:500], "status": "todo", "note": ""})
                l.plan = plan[:_PLAN_CAP]
            elif action == "plan_status":
                sid = str(kw.get("id") or "")
                st = str(kw.get("status") or "")
                if st not in _PLAN_STATUSES:
                    return {"error": f"status do passo deve ser um de {_PLAN_STATUSES}"}
                plan = [dict(s) for s in (l.plan or [])]
                hit = next((s for s in plan if s.get("id") == sid), None)
                if not hit:
                    return {"error": f"passo '{sid}' não existe"}
                hit["status"] = st
                if kw.get("note") is not None:
                    hit["note"] = str(kw["note"])[:300]
                l.plan = plan
            elif action == "note":
                text = str(kw.get("text") or "").strip()
                if not text:
                    return {"error": "informe 'text' da nota"}
                notes = list(l.notes or [])
                notes.append({"ts": _now(), "text": text[:1000]})
                l.notes = notes[-_NOTES_CAP:]
            elif action == "finding_add":
                text = str(kw.get("text") or "").strip()
                if not text:
                    return {"error": "informe 'text' do achado"}
                finds = list(l.findings or [])
                fid = _next_id(finds, "f")
                st = kw.get("status") if kw.get("status") in _FIND_STATUSES else "open"
                finds.append({"id": fid, "text": text[:800], "status": st,
                              "evidence": str(kw.get("evidence") or "")[:600]})
                l.findings = finds[:_FINDINGS_CAP]
            elif action == "finding_status":
                fid = str(kw.get("id") or "")
                st = str(kw.get("status") or "")
                if st not in _FIND_STATUSES:
                    return {"error": f"status do achado deve ser um de {_FIND_STATUSES}"}
                finds = [dict(f) for f in (l.findings or [])]
                hit = next((f for f in finds if f.get("id") == fid), None)
                if not hit:
                    return {"error": f"achado '{fid}' não existe"}
                hit["status"] = st
                if kw.get("evidence") is not None:
                    hit["evidence"] = str(kw["evidence"])[:600]
                l.findings = finds
            else:
                return {"error": f"ação desconhecida: {action}"}

            l.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(l)
            return {"ok": True, "ledger": _to_dict(l)}
    finally:
        await eng.dispose()


# lock POR-CHAT: serializa as mutações do mesmo ledger (a tool roda no threadpool, então
# duas chamadas paralelas p/ o mesmo chat — read-modify-write — poderiam colidir no INSERT
# do 1º ledger ou perder uma atualização). Um threading.Lock resolve no processo único (mesma
# premissa dos outros registros em memória: previews/exec_jobs/sessões).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _chat_lock(chat_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(chat_id)
        if lk is None:
            lk = threading.Lock()
            _locks[chat_id] = lk
        return lk


def apply(user_id: str, chat_id: str, project_id: str | None, action: str, **kw) -> dict:
    """Face síncrona p/ a tool SIFT (threadpool → asyncio.run + engine efêmera). Serializa
    por chat (sem corrida/lost-update) e blinda a tool de exceção crua — devolve erro limpo
    p/ o modelo em vez de estourar."""
    if not chat_id:
        return {"error": "sem chat vinculado — o ledger é por chat"}
    try:
        with _chat_lock(chat_id):
            return asyncio.run(_apply_async(user_id, chat_id, project_id, action, kw))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao atualizar o ledger: {str(exc)[:200]}"}
