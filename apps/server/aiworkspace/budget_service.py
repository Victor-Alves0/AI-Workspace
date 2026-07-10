"""Orçamento pessoal por usuário.

Cada usuário usa a PRÓPRIA chave de API (paga os próprios gastos), então isto NÃO
é um controle do admin — é uma proteção opt-in do próprio usuário contra susto na
fatura. O usuário define um teto mensal (em US$, a moeda que o OpenRouter cobra e
que o app exibe) e escolhe o comportamento ao atingir:
  - "warn":  só avisa (a UI mostra um banner); nada é bloqueado.
  - "pause": bloqueia novos turnos até o mês virar (ou ele aumentar/desligar).

O gasto do mês vem do ledger `usage_events` (soma de `cost` no mês corrente, UTC).
Config em `user.profile["budget"] = {enabled, monthly_usd, mode}`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UsageEvent, User


def _month_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_cost(db: AsyncSession, user_id) -> float:
    """Custo (US$) acumulado pelo usuário no mês corrente (UTC)."""
    total = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost), 0.0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= _month_start_utc(),
        )
    )
    return float(total or 0.0)


def _cfg(user: User) -> dict:
    b = (user.profile or {}).get("budget") or {}
    try:
        cap = float(b.get("monthly_usd") or 0.0)
    except (TypeError, ValueError):
        cap = 0.0
    mode = b.get("mode") if b.get("mode") in ("warn", "pause") else "warn"
    return {"enabled": bool(b.get("enabled")) and cap > 0, "cap": cap, "mode": mode}


async def budget_state(db: AsyncSession, user: User) -> dict:
    """Estado atual do orçamento do usuário (para a UI e o enforcement)."""
    cfg = _cfg(user)
    spent = await month_cost(db, user.id)
    over = cfg["enabled"] and spent >= cfg["cap"]
    return {
        "enabled": cfg["enabled"],
        "cap": round(cfg["cap"], 4),
        "spent": round(spent, 6),
        "mode": cfg["mode"],
        "over": over,
        # bloqueia de fato só quando estourou E o modo é "pausar"
        "blocked": bool(over and cfg["mode"] == "pause"),
    }


async def enforce_or_raise(db: AsyncSession, user: User) -> dict:
    """Chamado ANTES de iniciar um turno interativo. Levanta 402 quando o usuário
    estourou o teto no modo 'pausar'; caso contrário retorna o estado (o modo
    'avisar' nunca bloqueia — a UI mostra o banner)."""
    st = await budget_state(db, user)
    if st["blocked"]:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Orçamento mensal atingido (US$ {st['spent']:.2f} de US$ {st['cap']:.2f}). "
            f"Ajuste ou desligue o limite em Configurações → Conta.",
        )
    return st
