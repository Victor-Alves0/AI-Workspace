"""Analítica de uso do usuário: modelos mais usados, uso por dia, totais e
saldo de créditos do OpenRouter.

Tudo escopado ao próprio usuário (self-hosted multiusuário). As métricas saem do
ledger `usage_events` (uma linha por resposta) — sobrevive à exclusão de chats.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .config import get_settings
from .db import get_db
from .models import UsageEvent, User
from .providers.openrouter import _headers
from .secrets_service import OPENROUTER_KEY, get_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _openrouter_credits(api_key: str) -> dict | None:
    """Saldo do OpenRouter: {total, usage, remaining}. None se indisponível."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url}/credits", headers=_headers(api_key)
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
        total = float(data.get("total_credits", 0) or 0)
        usage = float(data.get("total_usage", 0) or 0)
        return {"total": total, "usage": usage, "remaining": total - usage}
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenRouter /credits falhou: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Janela temporal do gráfico "Uso" — o usuário escolhe o alcance e a granularidade
# se adapta (dia → semana → mês) p/ não gerar centenas de barras.
# --------------------------------------------------------------------------- #
_RANGES = {"7d", "30d", "6m", "1y", "all"}
_MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_WEEKDAYS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]  # Python weekday(): seg=0


def _sub_months(d: date, n: int) -> date:
    m = d.month - 1 - n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _next_month(d: date) -> date:
    return date(d.year + (d.month // 12), (d.month % 12) + 1, 1)


def _range_start(range_key: str, today: date, earliest: date | None) -> tuple[date, str]:
    """(início da janela, granularidade). 'all' começa no 1º evento e escolhe a
    granularidade pelo tamanho do período."""
    if range_key == "7d":
        return today - timedelta(days=6), "day"
    if range_key == "30d":
        return today - timedelta(days=29), "day"
    if range_key == "6m":
        return _sub_months(today, 6), "week"
    if range_key == "1y":
        return _sub_months(today, 12), "month"
    start = earliest or today
    span = (today - start).days
    gran = "day" if span <= 31 else "week" if span <= 200 else "month"
    return start, gran


def _bucket_key(d: date, gran: str) -> date:
    if gran == "day":
        return d
    if gran == "week":
        return d - timedelta(days=d.weekday())  # segunda-feira da semana
    return d.replace(day=1)


def _bucket_keys(start: date, today: date, gran: str) -> list[date]:
    keys: list[date] = []
    cur = _bucket_key(start, gran)
    while cur <= today:
        keys.append(cur)
        if gran == "day":
            cur += timedelta(days=1)
        elif gran == "week":
            cur += timedelta(days=7)
        else:
            cur = _next_month(cur)
    return keys


def _bucket_label(d: date, gran: str, range_key: str) -> str:
    if gran == "month":
        return f"{_MONTHS_PT[d.month - 1]}/{d.year % 100:02d}"
    if gran == "day" and range_key == "7d":
        return _WEEKDAYS_PT[d.weekday()]
    return f"{d.day:02d}/{d.month:02d}"


@router.get("/overview")
async def overview(
    range_: str = Query("7d", alias="range", description="7d | 30d | 6m | 1y | all"),
    tz_offset: int = Query(0, description="JS getTimezoneOffset() em minutos"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_approved),
):
    """Painel de analítica: modelos mais usados, uso por dia, totais e créditos.

    Lê do ledger `usage_events` (uma linha por resposta), que sobrevive à exclusão
    do chat — o histórico de uso não some quando o usuário apaga conversas."""
    rows = (
        await db.execute(
            select(
                UsageEvent.model, UsageEvent.model_name, UsageEvent.model_config_id,
                UsageEvent.total_tokens, UsageEvent.prompt_tokens,
                UsageEvent.completion_tokens, UsageEvent.reasoning_tokens,
                UsageEvent.cost, UsageEvent.created_at, UsageEvent.chat_id,
                UsageEvent.provider,
            )
            .where(UsageEvent.user_id == user.id)
            # ordena por tempo p/ que, ao agrupar por modelo, o NOME mais recente
            # (última escrita) prevaleça caso o usuário tenha renomeado o modelo.
            .order_by(UsageEvent.created_at)
        )
    ).all()

    # baldes temporais (locais, deslocando pelo tz_offset do navegador). A janela e
    # a granularidade dependem do 'range' escolhido pelo usuário.
    range_key = range_ if range_ in _RANGES else "7d"
    off = timedelta(minutes=tz_offset)
    local_today = (datetime.now(timezone.utc) - off).date()

    earliest: date | None = None
    if rows:
        c0 = rows[0].created_at
        if c0 is not None:
            c0 = c0 if c0.tzinfo else c0.replace(tzinfo=timezone.utc)
            earliest = (c0.astimezone(timezone.utc) - off).date()

    start, gran = _range_start(range_key, local_today, earliest)
    # janela ANTERIOR de mesmo tamanho (p/ o "▲/▼ % vs período anterior")
    span_days = (local_today - start).days + 1
    prev_start = start - timedelta(days=span_days)
    prev_end = start - timedelta(days=1)

    keys = _bucket_keys(start, local_today, gran)
    per_day = {
        d.isoformat(): {
            "date": d.isoformat(),
            "label": _bucket_label(d, gran, range_key),
            "tokens": 0, "cost": 0.0, "messages": 0,
            # empilhamento por modelo (preenchido depois com os top-N da janela)
            "by": {},
        }
        for d in keys
    }

    # --- agregações (volume por usuário é modesto; uma passada só) ---
    by_model: dict[str, dict] = {}       # DENTRO da janela
    chat_ids: set = set()                # conversas com uso na janela
    win = {"tokens": 0, "cost": 0.0, "messages": 0, "prompt": 0, "completion": 0, "reasoning": 0}
    prev = {"tokens": 0, "cost": 0.0, "messages": 0}
    activity_days: dict[str, int] = {}   # tokens por dia local — últimos 12 meses
    all_days: set[str] = set()           # dias com uso (streak, all-time)
    all_tokens = 0
    activity_start = _sub_months(local_today, 12)

    for model, model_name, model_config_id, tokens, prompt_t, completion_t, reason_t, cost, created_at, chat_id, provider in rows:
        tokens = int(tokens or 0)
        cost = float(cost or 0.0)
        all_tokens += tokens
        if created_at is None:
            continue
        cdt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        local_date = (cdt.astimezone(timezone.utc) - off).date()
        iso = local_date.isoformat()
        all_days.add(iso)
        if local_date >= activity_start:
            activity_days[iso] = activity_days.get(iso, 0) + tokens

        if prev_start <= local_date <= prev_end:
            prev["tokens"] += tokens
            prev["cost"] += cost
            prev["messages"] += 1
        if local_date < start:
            continue

        # ---- daqui p/ baixo: DENTRO da janela selecionada ----
        # chave ESTÁVEL (id do modelo custom, senão o id base) — renomear não fragmenta
        key = str(model_config_id or model or "desconhecido")
        name = (model_name or model or "desconhecido").strip() or "desconhecido"
        # fonte: deriva do id do modelo (cobre linhas antigas gravadas antes do fix)
        prov = "ollama" if (model or "").startswith("ollama/") else (provider or "openrouter")
        vendor = "local" if prov == "ollama" else ((model or "").split("/")[0] or "api")
        if chat_id is not None:
            chat_ids.add(chat_id)

        m = by_model.setdefault(key, {"id": key, "model": name, "provider": prov, "vendor": vendor,
                                      "messages": 0, "tokens": 0, "cost": 0.0})
        m["model"] = name
        m["messages"] += 1
        m["tokens"] += tokens
        m["cost"] += cost

        win["tokens"] += tokens
        win["cost"] += cost
        win["messages"] += 1
        win["prompt"] += int(prompt_t or 0)
        win["completion"] += int(completion_t or 0)
        win["reasoning"] += int(reason_t or 0)

        bk = _bucket_key(local_date, gran).isoformat()
        if bk in per_day:
            b = per_day[bk]
            b["tokens"] += tokens
            b["cost"] += cost
            b["messages"] += 1
            e = b["by"].setdefault(key, {"t": 0, "c": 0.0, "m": 0})
            e["t"] += tokens
            e["c"] += cost
            e["m"] += 1

    model_list = sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True)
    for m in model_list:
        m["pct"] = round(m["messages"] / win["messages"] * 100, 1) if win["messages"] else 0.0
        m["cost"] = round(m["cost"], 4)

    # série empilhada: top-5 modelos da janela + "outros" (o resto agregado)
    top_keys = [m["id"] for m in model_list[:5]]
    series = [{"id": m["id"], "name": m["model"], "provider": m["provider"]} for m in model_list[:5]]
    has_other = len(model_list) > 5
    if has_other:
        series.append({"id": "__other__", "name": "Outros", "provider": ""})
    per_day_list = []
    for d in per_day.values():
        by = d.pop("by")
        stack = {k: by[k] for k in top_keys if k in by}
        if has_other:
            ot = {"t": 0, "c": 0.0, "m": 0}
            for k, v in by.items():
                if k not in top_keys:
                    ot["t"] += v["t"]; ot["c"] += v["c"]; ot["m"] += v["m"]
            if ot["m"]:
                stack["__other__"] = ot
        d["by"] = {k: {"t": v["t"], "c": round(v["c"], 4), "m": v["m"]} for k, v in stack.items()}
        d["cost"] = round(d["cost"], 4)
        per_day_list.append(d)

    # --- atividade (12 meses) + sequência de dias consecutivos (all-time) ---
    longest = run = 0
    prev_day: date | None = None
    for iso in sorted(all_days):
        d = date.fromisoformat(iso)
        run = run + 1 if (prev_day is not None and d == prev_day + timedelta(days=1)) else 1
        longest = max(longest, run)
        prev_day = d
    act_total = sum(activity_days.values())
    first_act = min((date.fromisoformat(k) for k in activity_days), default=local_today)
    act_span = max(1, (local_today - max(first_act, activity_start)).days + 1)
    activity = {
        "start": activity_start.isoformat(),
        "days": [{"d": k, "t": v} for k, v in sorted(activity_days.items())],
        "longest_streak": longest,
        "avg_day": int(act_total / act_span),
        "avg_week": int(act_total / act_span * 7),
        "total_tokens": int(all_tokens),
    }

    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    credits = await _openrouter_credits(api_key) if api_key else None

    return {
        "by_model": model_list,
        "per_day": per_day_list,
        "series": series,
        "range": range_key,
        "granularity": gran,
        "credits": credits,
        "activity": activity,
        # variação vs o período anterior de mesmo tamanho ('all' não tem anterior)
        "prev_totals": None if range_key == "all" else {
            "tokens": prev["tokens"], "cost": round(prev["cost"], 4), "messages": prev["messages"],
        },
        "totals": {
            "chats": len(chat_ids),
            "messages": win["messages"],
            "tokens": int(win["tokens"]),
            "prompt_tokens": win["prompt"],
            "completion_tokens": win["completion"],
            "reasoning_tokens": win["reasoning"],
            "cost": round(win["cost"], 4),
            "avg_tokens": int(win["tokens"] / win["messages"]) if win["messages"] else 0,
        },
    }
