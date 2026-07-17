"""Scheduler in-process das automações.

Uma única task asyncio (iniciada no lifespan) varre o banco a cada ~15s e dispara
as automações vencidas (`enabled` e `next_run_at <= now`). Como o "quando rodar"
vive no banco, o reinício do processo retoma tudo sozinho — inclusive disparando
as que venceram durante o downtime (catch-up).

Isolamento: cada automação roda em sua própria task com try/except; uma falha não
derruba as demais nem o loop. Um guard em memória evita execuções sobrepostas da
mesma automação.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..db import SessionLocal
from ..models import Automation, Chat
from . import runner

logger = logging.getLogger(__name__)

_TICK_SECONDS = 15
# pisos anti-custo: intervalo mínimo entre disparos por tipo
_MIN_SCHEDULED = 60
_MIN_MONITOR = 60
# backoff ao falhar: base * 2^(falhas-1), com teto
_BACKOFF_CAP = 3600

_UNIT_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}

_task: asyncio.Task | None = None


def _schedule_seconds(schedule: dict) -> int:
    """Intervalo (s) de uma automação agendada, a partir de {every, unit}."""
    try:
        every = int((schedule or {}).get("every") or 1)
    except (TypeError, ValueError):
        every = 1
    unit = (schedule or {}).get("unit") or "hours"
    secs = max(1, every) * _UNIT_SECONDS.get(unit, 3600)
    return max(secs, _MIN_SCHEDULED)


def _between_seconds(schedule: dict) -> int:
    """Modo "between": SORTEIA um intervalo (s) aleatório na faixa [min, max] (na
    `unit`), recalculado a cada disparo. Piso anti-custo aplicado nas duas pontas."""
    sched = schedule or {}
    unit_s = _UNIT_SECONDS.get(sched.get("unit") or "hours", 3600)
    try:
        lo = max(1, int(sched.get("min") or 1))
        hi = max(1, int(sched.get("max") or 6))
    except (TypeError, ValueError):
        lo, hi = 1, 6
    if hi < lo:
        lo, hi = hi, lo
    lo_s = max(lo * unit_s, _MIN_SCHEDULED)
    hi_s = max(hi * unit_s, _MIN_SCHEDULED)
    return random.randint(lo_s, hi_s)


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = str(value or "09:00").split(":")
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except (ValueError, AttributeError):
        return 9, 0


def _next_at(schedule: dict, now: datetime) -> datetime:
    """Próxima ocorrência (UTC) p/ os modos com horário fixo (daily/weekly/monthly).

    O horário é interpretado no fuso do usuário via `tz_offset` (getTimezoneOffset
    do navegador, em minutos: UTC = local + offset; BRT=+180)."""
    try:
        off = int((schedule or {}).get("tz_offset") or 0)
    except (TypeError, ValueError):
        off = 0
    local_now = now - timedelta(minutes=off)
    hh, mm = _parse_hhmm(schedule.get("time"))
    mode = schedule.get("mode")

    if mode == "weekly":
        # dias no padrão JS getDay(): 0=domingo … 6=sábado
        try:
            days = sorted({int(d) % 7 for d in (schedule.get("days") or [])})
        except (TypeError, ValueError):
            days = []
        if not days:
            days = [(local_now.weekday() + 1) % 7]  # fallback: hoje
        for i in range(8):
            cand = (local_now + timedelta(days=i)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if (cand.weekday() + 1) % 7 in days and cand > local_now:
                return cand + timedelta(minutes=off)
        cand = local_now + timedelta(days=7)  # inalcançável, mas nunca falha
        return cand + timedelta(minutes=off)

    if mode == "monthly":
        try:
            day = max(1, min(31, int(schedule.get("day") or 1)))
        except (TypeError, ValueError):
            day = 1
        y, m = local_now.year, local_now.month
        for _ in range(2):  # este mês; senão, o próximo
            d = min(day, calendar.monthrange(y, m)[1])  # clampa p/ fev/meses de 30
            cand = local_now.replace(year=y, month=m, day=d, hour=hh, minute=mm, second=0, microsecond=0)
            if cand > local_now:
                return cand + timedelta(minutes=off)
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return cand + timedelta(minutes=off)

    # "daily"
    cand = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= local_now:
        cand += timedelta(days=1)
    return cand + timedelta(minutes=off)


def compute_next_run(automation: Automation, now: datetime) -> datetime:
    """Próximo horário de disparo após `now`."""
    if automation.kind == "monitor":
        secs = max(int(automation.interval_seconds or 300), _MIN_MONITOR)
        return now + timedelta(seconds=secs)
    mode = (automation.schedule or {}).get("mode")
    if mode in ("daily", "weekly", "monthly"):
        return _next_at(automation.schedule or {}, now)
    if mode == "between":
        return now + timedelta(seconds=_between_seconds(automation.schedule or {}))
    return now + timedelta(seconds=_schedule_seconds(automation.schedule))


def _backoff_next(automation: Automation, now: datetime) -> datetime:
    """Recuo exponencial após falhas consecutivas (limitado ao teto)."""
    if automation.kind == "monitor":
        base = max(int(automation.interval_seconds or 300), _MIN_MONITOR)
    elif (automation.schedule or {}).get("mode") == "between":
        base = _between_seconds(automation.schedule or {})
    else:
        base = _schedule_seconds(automation.schedule)
    factor = 2 ** max(0, automation.fail_count - 1)
    return now + timedelta(seconds=min(base * factor, _BACKOFF_CAP))


async def _run_one(automation_id) -> None:
    ok = True
    err: str | None = None
    try:
        res = await runner.run_automation(automation_id)
        # já rodando (disparo manual concorrente): não reagenda, tenta no próximo tick
        if isinstance(res, dict) and res.get("skipped") == "already_running":
            return
    except Exception as exc:  # noqa: BLE001 - registra e reagenda com backoff
        ok = False
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("Automação %s falhou: %s", automation_id, err)
    # atualiza os campos de agendamento numa sessão própria
    try:
        async with SessionLocal() as db:
            a = await db.get(Automation, automation_id)
            if a is not None:
                now = datetime.now(timezone.utc)
                a.last_run_at = now
                a.run_count = (a.run_count or 0) + 1
                if ok:
                    a.fail_count = 0
                    a.last_error = None
                    # one-shot (lembretes): dispara uma vez e se desativa
                    if a.kind == "reminder" or (a.schedule or {}).get("one_shot"):
                        a.enabled = False
                        a.next_run_at = None
                    else:
                        a.next_run_at = compute_next_run(a, now)
                else:
                    a.fail_count = (a.fail_count or 0) + 1
                    a.last_error = (err or "")[:1000]
                    a.next_run_at = _backoff_next(a, now)
                await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao reagendar automação %s", automation_id)


async def _tick() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(Automation).where(
                Automation.enabled.is_(True),
                Automation.next_run_at.is_not(None),
                Automation.next_run_at <= now,
            )
        )
        due = [a.id for a in rows]
        # "Duração do Chat": apaga chats vencidos (criados por automações com TTL).
        # DELETE direto — as mensagens caem pelo ON DELETE CASCADE do banco.
        deleted = await db.execute(
            delete(Chat).where(Chat.expires_at.is_not(None), Chat.expires_at <= now)
        )
        if deleted.rowcount:
            await db.commit()
            logger.info("Chats expirados removidos: %s", deleted.rowcount)
    for aid in due:
        if runner.is_running(aid):
            continue  # já em execução (guard compartilhado) — próximo tick tenta
        asyncio.create_task(_run_one(aid))


async def _loop() -> None:
    logger.info("Scheduler de automações iniciado (tick=%ss)", _TICK_SECONDS)
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - o loop nunca deve morrer por uma falha de tick
            logger.exception("Erro no tick do scheduler")
        await asyncio.sleep(_TICK_SECONDS)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
