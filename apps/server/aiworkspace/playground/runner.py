"""Execução de benchmarks em background.

Um `BenchmarkRun` roda cada caso contra cada modelo escolhido:
  1. chama o modelo (`openrouter.complete_verbose`) medindo latência/tokens/custo;
  2. aplica a regra do caso (`contains`/`regex` esperado → pass/fail), se houver;
  3. se o benchmark tem `judge_model`, pede a nota (0-100) ao juiz LLM.
Grava `results`/`aggregates` incrementalmente (a UI faz poll). Erros por célula não
derrubam a run. Cada chamada é registrada no ledger de uso (`usage_events`), então o
gasto do benchmark aparece na Analítica e conta no orçamento.

Roda num engine efêmero (NullPool) porque o pool async do app é preso ao loop da
request — mesmo padrão de `automation/creator.py`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..models import Benchmark, BenchmarkRun, ModelConfig, User
from ..providers import openrouter
from ..secrets_service import OPENROUTER_KEY, get_secret
from ..integrations import ollama_service
from ..usage_service import usage_event_from_record

logger = logging.getLogger(__name__)


def model_key(idx: int) -> str:
    """Chave estável de um modelo dentro da run (index-based: tolera modelos repetidos)."""
    return f"m{idx}"


async def _resolve(db, user: User, model: str) -> tuple[str | None, str | None, str | None]:
    """(api_key, base_url, error). Não levanta — devolve erro legível na célula."""
    if (model or "").startswith(ollama_service.MODEL_PREFIX):
        base = await ollama_service.get_base_url(db, user.id)
        if not base:
            return None, None, "Ollama não configurado"
        return "ollama", base.rstrip("/") + "/v1", None
    key = await get_secret(db, user.id, OPENROUTER_KEY)
    if not key:
        return None, None, "Chave do OpenRouter ausente"
    return key, None, None


def _apply_rule(expected: dict | None, text: str, tools_used: list[str] | None = None,
                tool_blobs: str = "") -> bool | None:
    """pass/fail da regra do caso; None = sem regra (nada a checar).

    Regras de TEXTO: contains/regex sobre a resposta final.
    Regras de DECISÃO (suíte de tool use): avaliam O QUE o modelo chamou —
      - tool_called:     `value` aparece no nome de alguma tool chamada OU dentro
                         do código passado ao run_code (code mode chama a tool real
                         por dentro do sandbox);
      - tool_not_called: negação da anterior;
      - no_tool:         nenhuma tool foi chamada (search_tools/descoberta não
                         conta — descobrir e decidir NÃO usar é decisão correta)."""
    if not isinstance(expected, dict):
        return None
    mode = (expected.get("mode") or "none").lower()
    value = str(expected.get("value") or "")
    if mode == "none":
        return None
    if mode in ("tool_called", "tool_not_called", "no_tool"):
        used = [t for t in (tools_used or []) if t]
        real = [t for t in used if t not in ("search_tools",)]
        if mode == "no_tool":
            return not real
        if not value:
            return None
        # normaliza p/ casar "research.deep.run" com o nome flat "research__deep__run"
        needle = value.lower().replace(".", "__")
        hay = " ".join(used).lower().replace(".", "__") + " " + tool_blobs.lower().replace(".", "__")
        hit = needle in hay
        return hit if mode == "tool_called" else not hit
    if not value:
        return None
    if mode == "contains":
        return value.lower() in (text or "").lower()
    if mode == "regex":
        try:
            return re.search(value, text or "", re.IGNORECASE | re.DOTALL) is not None
        except re.error:
            return None
    return None


# teto de parede de uma célula com tools (turno agêntico pode iterar várias vezes)
_TOOL_CELL_TIMEOUT = 240


async def _tool_cell(db, user: User, r: dict, prompt: str, case_sys: str) -> dict[str, Any]:
    """Roda UM caso como turno agêntico REAL (com as tools do preset) e devolve a
    célula: texto final + latência + usage + `tools_used` (nomes chamados) +
    `tool_blobs` (args do run_code, p/ as regras de decisão enxergarem a tool real
    chamada por dentro do sandbox). É a suíte de DECISÃO: mede o que o modelo
    escolheu chamar, não só o que respondeu."""
    from ..chat.orchestrator import TurnSession, run_turn
    from ..chat.turn_setup import _code_mode, _load_skills
    from ..tools.loader import get_sift_for_user

    mc = r["mc"]
    sift = await get_sift_for_user(db, user.id, mc)
    skills = await _load_skills(db, user, mc)
    sys_parts = [p for p in [(mc.system_prompt if mc else ""), case_sys] if p]

    text = ""
    usage: dict[str, Any] = {}
    tools_used: list[str] = []
    blobs: list[str] = []
    t0 = time.monotonic()

    # segura o gerador p/ fechá-lo explicitamente no timeout — senão o stream do
    # OpenRouter (e a tool no threadpool) continuaria rodando/pagando após o corte.
    gen = run_turn(
        api_key=r["key"], model=r["model"], history=[], user_text=prompt,
        chat_system_prompt="\n\n".join(sys_parts) or None,
        params=(mc.params if mc else {}) or {},
        session=TurnSession(user_id=str(user.id), background=True),
        base_url=r["base_url"], sift=sift, code_mode=_code_mode(mc),
        skills=skills, use_context=False,
    )

    async def _drive():
        nonlocal text, usage
        async for ev in gen:
            t = ev.get("type")
            if t == "tool_call":
                name = str(ev.get("name") or "")
                tools_used.append(name)
                if name == "run_code":
                    blobs.append(str((ev.get("arguments") or {}).get("code") or ""))
            elif t == "done":
                text = ev.get("content") or text
                usage = ev.get("usage") or {}
            elif t == "error":
                raise RuntimeError(ev.get("message") or "falha no modelo")

    try:
        await asyncio.wait_for(_drive(), timeout=_TOOL_CELL_TIMEOUT)
    finally:
        await gen.aclose()  # encerra o turno subjacente (no-op se já terminou)
    return {
        "text": text,
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cost": float(usage.get("cost") or 0.0),
        "tools_used": tools_used,
        "_tool_blobs": " ".join(blobs),
    }


_SCORE_RE = re.compile(r"score\s*[:=]?\s*(\d{1,3})", re.IGNORECASE)
_REASON_RE = re.compile(r"reason\s*[:=]?\s*(.+)", re.IGNORECASE | re.DOTALL)


async def _judge(
    db, user: User, judge_model: str, prompt: str, answer: str, criteria: str
) -> tuple[int | None, str, dict]:
    """(nota 0-100, justificativa, usage). Best-effort: falha do juiz → (None, msg, {})."""
    key, base_url, err = await _resolve(db, user, judge_model)
    if err:
        return None, f"juiz indisponível: {err}", {}
    sys = (
        "You are a strict grader. Given a prompt, a candidate answer and a grading "
        "criterion, rate how well the answer meets the criterion from 0 to 100. "
        "Reply EXACTLY in this format and nothing else:\nSCORE: <0-100>\nREASON: <one short sentence>"
    )
    crit = criteria.strip() or "overall quality, correctness and relevance"
    usr = f"PROMPT:\n{prompt}\n\nCANDIDATE ANSWER:\n{answer}\n\nCRITERION:\n{crit}"
    res = await openrouter.complete_verbose(
        key, judge_model, [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        params={"temperature": 0}, base_url=base_url,
    )
    if res.get("error"):
        return None, f"juiz falhou: {res['error']}", {}
    out = res.get("text") or ""
    m = _SCORE_RE.search(out)
    score = max(0, min(100, int(m.group(1)))) if m else None
    rm = _REASON_RE.search(out)
    reason = (rm.group(1).strip() if rm else out.strip())[:400]
    usage = {
        "prompt_tokens": res.get("prompt_tokens") or 0,
        "completion_tokens": res.get("completion_tokens") or 0,
        "cost": res.get("cost") or 0.0,
    }
    return score, reason, usage


def _log_usage(db, user_id, model: str, mc_id: str | None, usage: dict) -> None:
    """Registra uma chamada no ledger (analítica + orçamento). chat/message = None."""
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    rec = {
        "model": model,
        "model_config_id": mc_id,
        "model_name": model,
        "provider": "ollama" if (model or "").startswith("ollama/") else "openrouter",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost": float(usage.get("cost") or 0.0),
    }
    uev = usage_event_from_record(user_id, None, None, rec)
    if uev is not None:
        db.add(uev)


def _aggregate(models: list[dict], cases: list[dict], results: dict) -> dict:
    """Agregados por modelo: médias de latência/nota, totais de tokens/custo, pass rate."""
    agg: dict[str, Any] = {}
    for idx, _m in enumerate(models):
        mk = model_key(idx)
        cells = [results.get(f"{c.get('id')}|{mk}") for c in cases]
        cells = [c for c in cells if isinstance(c, dict) and not c.get("error")]
        n = len(cells)
        if not n:
            agg[mk] = {"count": 0}
            continue
        lat = [c.get("latency_ms") or 0 for c in cells]
        toks = sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in cells)
        cost = sum(c.get("cost") or 0.0 for c in cells)
        rules = [c["rule_pass"] for c in cells if isinstance(c.get("rule_pass"), bool)]
        judges = [c["judge_score"] for c in cells if isinstance(c.get("judge_score"), int)]
        agg[mk] = {
            "count": n,
            "avg_latency": round(sum(lat) / n),
            "total_tokens": toks,
            "total_cost": round(cost, 6),
            "pass_rate": round(sum(1 for r in rules if r) / len(rules), 3) if rules else None,
            "avg_judge": round(sum(judges) / len(judges), 1) if judges else None,
        }
    return agg


async def run_benchmark(run_id: uuid.UUID | str) -> None:
    rid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            run = await db.get(BenchmarkRun, rid)
            if run is None:
                return
            bench = await db.get(Benchmark, run.benchmark_id)
            user = await db.get(User, run.user_id)
            if bench is None or user is None:
                run.status = "error"
                run.error = "benchmark ou usuário não encontrado"
                await db.commit()
                return
            cases = list(bench.cases or [])
            models = list(run.models or [])
            judge_model = (bench.judge_model or "").strip()
            results: dict[str, Any] = dict(run.results or {})

            # pré-resolve preset + provedor de cada modelo (uma vez). Preset SEM
            # modelo base explícito (slot "custom:") usa o base_model do preset.
            resolved: list[dict] = []
            for m in models:
                mc = None
                if m.get("model_config_id"):
                    try:
                        mc = await db.get(ModelConfig, uuid.UUID(str(m["model_config_id"])))
                        if mc is not None and mc.user_id != user.id:
                            mc = None
                    except (ValueError, TypeError):
                        mc = None
                mstr = (m.get("model") or "") or (mc.base_model if mc else "")
                key, base_url, err = await _resolve(db, user, mstr)
                if not mstr and not err:
                    err = "modelo não definido"
                resolved.append({"model": mstr, "key": key, "base_url": base_url,
                                 "err": err, "mc": mc, "mc_id": m.get("model_config_id"),
                                 # suíte de decisão: célula roda como turno agêntico
                                 "tools": bool(m.get("tools")) and mc is not None})

            try:
                for c in cases:
                    cid = c.get("id")
                    prompt = str(c.get("prompt") or "")
                    case_sys = str(c.get("system") or "")
                    for idx, r in enumerate(resolved):
                        mk = model_key(idx)
                        cell_key = f"{cid}|{mk}"
                        if r["err"]:
                            results[cell_key] = {"error": r["err"]}
                            run.results = dict(results)
                            flag_modified(run, "results")
                            await db.commit()
                            continue
                        mc = r["mc"]
                        # modo TOOLS (suíte de decisão): turno agêntico real com as
                        # ferramentas do preset; senão, completion pura (mais barato)
                        if r.get("tools") and mc is not None:
                            try:
                                cell = await _tool_cell(db, user, r, prompt, case_sys)
                            except Exception as exc:  # noqa: BLE001 - célula não derruba a run
                                results[cell_key] = {"error": str(exc)[:400]}
                                run.results = dict(results)
                                flag_modified(run, "results")
                                await db.commit()
                                continue
                            text = cell.get("text") or ""
                            tool_blobs = cell.pop("_tool_blobs", "")
                            _log_usage(db, user.id, r["model"], r["mc_id"], cell)
                            rule = _apply_rule(
                                c.get("expected"), text,
                                tools_used=cell.get("tools_used"), tool_blobs=tool_blobs,
                            )
                            if rule is not None:
                                cell["rule_pass"] = rule
                        else:
                            sys_parts = [p for p in [(mc.system_prompt if mc else ""), case_sys] if p]
                            msgs: list[dict] = []
                            if sys_parts:
                                msgs.append({"role": "system", "content": "\n\n".join(sys_parts)})
                            msgs.append({"role": "user", "content": prompt})
                            res = await openrouter.complete_verbose(
                                r["key"], r["model"], msgs,
                                params=(mc.params if mc else {}) or {}, base_url=r["base_url"],
                            )
                            if res.get("error"):
                                results[cell_key] = {"error": res["error"], "latency_ms": res.get("latency_ms")}
                                run.results = dict(results)
                                flag_modified(run, "results")
                                run.aggregates = _aggregate(models, cases, results)
                                flag_modified(run, "aggregates")
                                await db.commit()
                                continue
                            text = res.get("text") or ""
                            cell = {
                                "text": text,
                                "latency_ms": res.get("latency_ms"),
                                "prompt_tokens": res.get("prompt_tokens"),
                                "completion_tokens": res.get("completion_tokens"),
                                "cost": res.get("cost"),
                            }
                            _log_usage(db, user.id, r["model"], r["mc_id"], {
                                "prompt_tokens": res.get("prompt_tokens"),
                                "completion_tokens": res.get("completion_tokens"),
                                "cost": res.get("cost"),
                            })
                            rule = _apply_rule(c.get("expected"), text)
                            if rule is not None:
                                cell["rule_pass"] = rule
                        # juiz + gravação valem p/ os DOIS ramos (tools e completion)
                        if judge_model:
                            score, reason, jusage = await _judge(
                                db, user, judge_model, prompt, text, str(c.get("judge_criteria") or "")
                            )
                            if score is not None:
                                cell["judge_score"] = score
                            cell["judge_reason"] = reason
                            if jusage:
                                _log_usage(db, user.id, judge_model, None, jusage)
                        results[cell_key] = cell
                        run.results = dict(results)
                        flag_modified(run, "results")
                        run.aggregates = _aggregate(models, cases, results)
                        flag_modified(run, "aggregates")
                        await db.commit()

                run.status = "done"
                run.aggregates = _aggregate(models, cases, results)
                flag_modified(run, "aggregates")
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Benchmark run falhou")
                run.status = "error"
                run.error = str(exc)[:500]
                await db.commit()
    finally:
        await eng.dispose()
