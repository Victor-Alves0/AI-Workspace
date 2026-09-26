"""Equipes de subagentes: muitos agentes criados pela IA numa chamada só (`delegate_team`).

Três peças, usadas tanto pelo turno quanto pelos trabalhos em segundo plano:

- `SubagentPool`: o teto de agentes do TURNO inteiro (a árvore toda, sub-equipes incluídas),
  a concorrência (um semáforo POR NÍVEL: o líder segura a vaga dele enquanto espera a
  equipe, que usa as vagas do nível de baixo, então nunca há impasse), um lock para a
  sessão de banco do turno (AsyncSession não aceita uso concorrente) e o orçamento.
- `run_team`: roda os membros (a fila é o semáforo do pool), repassa o progresso de cada
  um e junta os relatórios.
- síntese em camadas: se os relatórios não cabem no contexto do orquestrador, agentes de
  síntese os resumem em grupos, e de novo, até caber (map-reduce).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

MAX_AGENTS = 1000
MAX_CONCURRENCY = 64
# o que volta ao orquestrador: relatórios inteiros até aqui; acima, síntese em camadas
_REPORT_BUDGET = 40_000
_SYNTH_CHUNK = 30_000
# o que fica guardado para a UI (tool_events no banco): dividido entre os membros
_STORE_OUTPUT = 3_000_000
_STORE_TIMELINE = 2_000_000
# em cadeia: quanto dos relatórios anteriores cada membro recebe
_CHAIN_CONTEXT = 30_000


class SubagentPool:
    def __init__(self, limit: int, concurrency: int, user_id: Any = None):
        self.limit = max(1, min(int(limit), MAX_AGENTS))
        self.remaining = self.limit
        self.concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
        self.user_id = user_id
        self.db_lock = asyncio.Lock()
        self.cache: dict[str, Any] = {}
        self._sems: dict[int, asyncio.Semaphore] = {}
        self._budget_at = 0.0
        self._budget_blocked = False

    def take(self) -> bool:
        """Reserva um agente do teto do turno."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True

    def slot(self, depth: int) -> asyncio.Semaphore:
        if depth not in self._sems:
            self._sems[depth] = asyncio.Semaphore(self.concurrency)
        return self._sems[depth]

    def fork(self) -> SubagentPool:
        """Pool de um trabalho em segundo plano: sessão de banco própria (lock novo),
        mesmo teto restante."""
        p = SubagentPool(self.remaining or 1, self.concurrency, self.user_id)
        p.remaining = self.remaining
        return p

    async def budget_blocked(self) -> bool:
        """Orçamento mensal estourado no modo "pausar"? Consulta no máximo a cada 15s,
        numa sessão própria (a do turno pode estar ocupada por outro agente)."""
        if self.user_id is None:
            return False
        now = time.monotonic()
        if now - self._budget_at < 15:
            return self._budget_blocked
        self._budget_at = now
        try:
            from ..budget_service import budget_state
            from ..db import SessionLocal
            from ..models import User

            async with SessionLocal() as s:
                u = await s.get(User, self.user_id)
                self._budget_blocked = bool(u and (await budget_state(s, u)).get("blocked"))
        except Exception:  # noqa: BLE001 - na dúvida não bloqueia
            self._budget_blocked = False
        return self._budget_blocked


def team_members(args: dict) -> tuple[list[dict], str | None]:
    """Normaliza os membros pedidos pela IA: [{name, instructions, task, isolated}]."""
    shared = str(args.get("shared_instructions") or "").strip()
    raw = args.get("members")
    if not isinstance(raw, list) or not raw:
        return [], "`members` precisa ser uma lista com pelo menos um agente"
    out: list[dict] = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            continue
        name = " ".join(str(m.get("name") or f"Agente {i + 1}").split())[:60]
        task = str(m.get("task") or "").strip()
        if not task:
            continue
        instr = "\n\n".join(p for p in (shared, str(m.get("instructions") or "").strip()) if p)
        out.append({"name": name, "task": task, "instructions": instr[:8000] or "Do the task well.",
                    "isolated": bool(m.get("isolated"))})
    if not out:
        return [], "nenhum membro tem `task`"
    return out, None


def _trim_timeline(timeline: list[dict], cap: int) -> list[dict]:
    out, used = [], 0
    for it in timeline or []:
        if it.get("kind") in ("reasoning", "text"):
            txt = str(it.get("text") or "")
            if used >= cap:
                continue
            if used + len(txt) > cap:
                txt = txt[: cap - used] + " […]"
            used += len(txt)
            out.append({**it, "text": txt})
        else:
            out.append(it)
    return out


async def run_team(
    run: Callable[..., Awaitable[dict]],
    members: list[dict],
    goal: str,
    emit: Callable[[dict], None],
    synthesize: Callable[[str, str], Awaitable[str]] | None,
    chain: bool = False,
) -> dict:
    """Roda a equipe e devolve o resultado (kind `subagent_team`). `emit` recebe os eventos
    de cada membro: {"member": i, "status": "running"|"progress"|"done", ...}.
    `chain`: em sequência, e cada membro recebe os relatórios dos anteriores (etapas que
    dependem umas das outras); senão, todos ao mesmo tempo (a fila do pool limita)."""
    n = len(members)
    results: list[dict | None] = [None] * n

    def _task_with_prior(i: int, m: dict) -> str:
        anteriores = [
            f"### {members[j]['name']}\n{(results[j] or {}).get('output') or (results[j] or {}).get('error') or ''!s}"
            for j in range(i)
        ]
        if not anteriores:
            return m["task"]
        contexto = "\n\n".join(anteriores)
        if len(contexto) > _CHAIN_CONTEXT:
            contexto = "[…]\n" + contexto[-_CHAIN_CONTEXT:]
        return f"{m['task']}\n\nWork from the previous steps of your team (build on it):\n\n{contexto}"

    async def _one(i: int, m: dict) -> None:
        def prog(ev: dict, _i: int = i) -> None:
            if ev.get("state") == "running":
                emit({"member": _i, "status": "running"})
            else:
                emit({"member": _i, "status": "progress", **ev})
        try:
            tarefa = _task_with_prior(i, m) if chain else m["task"]
            res = await run("new", tarefa, {"name": m["name"], "instructions": m["instructions"],
                                                "isolated": m["isolated"],
                                                # em paralelo, no MESMO código: só leitura
                                                "read_only": not chain and not m["isolated"]},
                            progress=prog)
        except Exception as exc:  # noqa: BLE001 - a falha vira o resultado do membro
            logger.warning("membro da equipe falhou: %s", exc)
            res = {"error": f"o subagente falhou: {exc}"}
        results[i] = res if isinstance(res, dict) else {"output": str(res)}
        emit({"member": i, "status": "done", "ok": not results[i].get("error")})

    if chain:
        for i, m in enumerate(members):
            await _one(i, m)
    else:
        tarefas = [asyncio.ensure_future(_one(i, m)) for i, m in enumerate(members)]
        try:
            await asyncio.gather(*tarefas)
        finally:
            for t in tarefas:
                if not t.done():
                    t.cancel()

    out_cap = max(2_000, min(50_000, _STORE_OUTPUT // max(1, n)))
    tl_cap = max(800, min(20_000, _STORE_TIMELINE // max(1, n)))
    stored: list[dict] = []
    reports: list[str] = []
    ok = 0
    for m, r in zip(members, results):
        r = r or {"error": "sem resultado"}
        err = r.get("error")
        saida = str(r.get("output") or "")
        if not err:
            ok += 1
            reports.append(f"### {m['name']}\nTask: {m['task'][:300]}\n\n{saida}")
        else:
            reports.append(f"### {m['name']}\nTask: {m['task'][:300]}\n\nFAILED: {err}")
        item = {"agent": r.get("agent") or m["name"], "task": m["task"], "adhoc": True,
                "output": saida[:out_cap] + (" […]" if len(saida) > out_cap else ""),
                "timeline": _trim_timeline(r.get("timeline") or [], tl_cap)}
        for k in ("error", "task_id"):
            if r.get(k):
                item[k] = r[k]
        stored.append(item)

    joined = "\n\n".join(reports)
    report = joined
    synthesized = False
    if len(joined) > _REPORT_BUDGET and synthesize is not None:
        emit({"status": "synthesis"})
        report = await _reduce(reports, goal, synthesize)
        synthesized = True
    elif len(joined) > _REPORT_BUDGET:
        report = joined[:_REPORT_BUDGET] + "\n[relatórios cortados]"
    return {"kind": "subagent_team", "goal": goal, "size": n, "succeeded": ok, "failed": n - ok,
            "report": report, "synthesized": synthesized, "chained": chain, "members": stored}


async def _reduce(reports: list[str], goal: str, synthesize: Callable[[str, str], Awaitable[str]]) -> str:
    """Resume os relatórios em grupos que cabem num contexto, e repete até caber."""
    nivel = 0
    while True:
        joined = "\n\n".join(reports)
        if len(joined) <= _REPORT_BUDGET or len(reports) <= 1:
            return joined[: _REPORT_BUDGET * 2]
        grupos: list[list[str]] = [[]]
        tam = 0
        for r in reports:
            r = r[:_SYNTH_CHUNK]
            if grupos[-1] and tam + len(r) > _SYNTH_CHUNK:
                grupos.append([])
                tam = 0
            grupos[-1].append(r)
            tam += len(r)
        nivel += 1
        resumos = await asyncio.gather(*[
            synthesize(goal, "\n\n".join(g)) for g in grupos
        ], return_exceptions=True)
        reports = [
            f"### Síntese {nivel}.{i + 1}\n\n" + (s if isinstance(s, str) else "\n\n".join(g)[:4000])
            for i, (s, g) in enumerate(zip(resumos, grupos))
        ]
        if nivel >= 4:
            return "\n\n".join(reports)[: _REPORT_BUDGET * 2]


def model_view(result: dict) -> dict:
    """O que o orquestrador vê de uma equipe: o relatório (ou a síntese) e o placar."""
    view = {k: result.get(k) for k in ("goal", "size", "succeeded", "failed", "report", "synthesized", "chained")}
    membros = result.get("members") or []
    if len(membros) <= 60:
        view["members"] = [
            {"agent": m.get("agent"), "ok": not m.get("error"), **({"task_id": m["task_id"]} if m.get("task_id") else {})}
            for m in membros
        ]
    if any(m.get("task_id") for m in membros):
        view["note"] = ("Some members worked on isolated worktrees: their changes are tasks awaiting "
                        "the user's review in the Tasks tab, not merged yet.")
    return view
