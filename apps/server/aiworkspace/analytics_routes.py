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
            )
            .where(UsageEvent.user_id == user.id)
            # ordena por tempo p/ que, ao agrupar por modelo, o NOME mais recente
            # (última escrita) prevaleça caso o usuário tenha renomeado o modelo.
            .order_by(UsageEvent.created_at)
        )
    ).all()

    # nº de conversas que geraram uso (distintas no ledger — histórico, não some
    # com a exclusão do chat).
    chat_ids: set = set()

    # --- agregações em memória (volume por usuário é modesto) ---
    by_model: dict[str, dict] = {}
    tot_tokens = tot_cost = 0.0
    tot_reasoning = 0
    tot_prompt = tot_completion = 0

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
    keys = _bucket_keys(start, local_today, gran)
    per_day = {
        d.isoformat(): {
            "date": d.isoformat(),
            "label": _bucket_label(d, gran, range_key),
            "tokens": 0, "cost": 0.0, "messages": 0,
        }
        for d in keys
    }

    for model, model_name, model_config_id, tokens, prompt_t, completion_t, reason_t, cost, created_at, chat_id in rows:
        # agrupa por chave ESTÁVEL (id do modelo custom, senão o id base do modelo),
        # não pelo nome de exibição — renomear um modelo não fragmenta o histórico.
        key = str(model_config_id or model or "desconhecido")
        name = (model_name or model or "desconhecido").strip() or "desconhecido"
        tokens = int(tokens or 0)
        cost = float(cost or 0.0)
        prompt_t = int(prompt_t or 0)
        completion_t = int(completion_t or 0)
        reason_t = int(reason_t or 0)
        if chat_id is not None:
            chat_ids.add(chat_id)

        m = by_model.setdefault(key, {"id": key, "model": name, "messages": 0, "tokens": 0, "cost": 0.0})
        m["model"] = name  # rows ordenadas por tempo → nome mais recente vence
        m["messages"] += 1
        m["tokens"] += tokens
        m["cost"] += cost

        tot_tokens += tokens
        tot_cost += cost
        tot_reasoning += reason_t
        tot_prompt += prompt_t
        tot_completion += completion_t

        if created_at is not None:
            # created_at é tz-aware (UTC); normaliza p/ o dia local do usuário e
            # cai no balde (dia/semana/mês) correspondente da janela selecionada.
            cdt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            local_date = (cdt.astimezone(timezone.utc) - off).date()
            bk = _bucket_key(local_date, gran).isoformat()
            if bk in per_day:
                per_day[bk]["tokens"] += tokens
                per_day[bk]["cost"] += cost
                per_day[bk]["messages"] += 1

    total_messages = sum(m["messages"] for m in by_model.values())
    model_list = sorted(by_model.values(), key=lambda x: x["messages"], reverse=True)
    for m in model_list:
        m["pct"] = round(m["messages"] / total_messages * 100, 1) if total_messages else 0.0
        m["cost"] = round(m["cost"], 4)

    per_day_list = list(per_day.values())
    for d in per_day_list:
        d["cost"] = round(d["cost"], 4)

    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    credits = await _openrouter_credits(api_key) if api_key else None

    return {
        "by_model": model_list,
        "per_day": per_day_list,
        "range": range_key,
        "granularity": gran,
        "credits": credits,
        "totals": {
            "chats": len(chat_ids),
            "messages": total_messages,
            "tokens": int(tot_tokens),
            "prompt_tokens": tot_prompt,
            "completion_tokens": tot_completion,
            "reasoning_tokens": tot_reasoning,
            "cost": round(tot_cost, 4),
            "avg_tokens": int(tot_tokens / total_messages) if total_messages else 0,
        },
    }
