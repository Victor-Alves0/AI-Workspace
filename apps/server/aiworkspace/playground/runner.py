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

import logging
import re
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


def _apply_rule(expected: dict | None, text: str) -> bool | None:
    """pass/fail da regra do caso; None = sem regra (nada a checar)."""
    if not isinstance(expected, dict):
        return None
    mode = (expected.get("mode") or "none").lower()
    value = str(expected.get("value") or "")
    if mode == "none" or not value:
        return None
    if mode == "contains":
        return value.lower() in (text or "").lower()
    if mode == "regex":
        try:
            return re.search(value, text or "", re.IGNORECASE | re.DOTALL) is not None
        except re.error:
            return None
    return None


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

            # pré-resolve provedor + preset de cada modelo (uma vez)
            resolved: list[dict] = []
            for m in models:
                mstr = m.get("model") or ""
                key, base_url, err = await _resolve(db, user, mstr)
                mc = None
                if m.get("model_config_id"):
                    try:
                        mc = await db.get(ModelConfig, uuid.UUID(str(m["model_config_id"])))
                        if mc is not None and mc.user_id != user.id:
                            mc = None
                    except (ValueError, TypeError):
                        mc = None
                resolved.append({"model": mstr, "key": key, "base_url": base_url,
                                 "err": err, "mc": mc, "mc_id": m.get("model_config_id")})

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
                        else:
                            text = res.get("text") or ""
                            cell: dict[str, Any] = {
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
