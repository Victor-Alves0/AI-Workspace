"""Limites por chave: taxa, cotas, concorrência, tokens e orçamento.

Dois relógios diferentes, de propósito:

  - **Curto prazo** (requisições por minuto, concorrência) vive em MEMÓRIA. É
    checado a cada requisição e precisa ser barato; um round-trip ao banco por
    chamada só para contar dobraria a latência do endpoint mais quente.
  - **Longo prazo** (dia, mês, tokens, US$) vem do BANCO (`api_requests` /
    `usage_events`). Esses limites protegem a fatura, então têm de sobreviver a um
    restart do processo — contá-los em memória seria zerar o teto a cada deploy.

Consequência assumida: com várias réplicas do servidor, o RPM é por réplica. Para
uma instância self-hosted (o caso deste app) isso é exato; se um dia houver mais de
uma réplica, o eixo diário/mensal continua correto e o RPM vira aproximado.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApiRequest, UsageEvent

_lock = threading.Lock()
# chave -> timestamps das requisições recentes (janela deslizante de 60s)
_hits: dict[str, deque[float]] = defaultdict(deque)
# chave -> requisições em voo (concorrência)
_inflight: dict[str, int] = defaultdict(int)
_WINDOW = 60.0
_MAX_KEYS = 5_000


class LimitError(Exception):
    """Limite estourado. `retry_after` alimenta o header Retry-After (segundos)."""

    def __init__(self, message: str, *, code: str, retry_after: int | None = None,
                 status: int = 429):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retry_after = retry_after
        self.status = status


def _limit(key: Any, name: str) -> int:
    try:
        return max(0, int((key.limits or {}).get(name) or 0))
    except (TypeError, ValueError):
        return 0


def _budget(key: Any) -> float:
    try:
        return max(0.0, float((key.limits or {}).get("budget_usd") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _purge(now: float) -> None:
    """Descarta chaves inativas (chamar com _lock). Sem isto, uma chave usada uma
    vez ficaria ocupando memória para sempre."""
    dead = [k for k, dq in _hits.items() if not dq or now - dq[-1] > _WINDOW]
    for k in dead:
        _hits.pop(k, None)
        if not _inflight.get(k):
            _inflight.pop(k, None)


def check_rate(key: Any) -> None:
    """Requisições por minuto (janela deslizante). Registra o hit se passar."""
    rpm = _limit(key, "rpm")
    if not rpm:
        return
    kid = str(key.id)
    now = time.time()
    with _lock:
        if len(_hits) >= _MAX_KEYS and kid not in _hits:
            _purge(now)
        dq = _hits[kid]
        while dq and now - dq[0] > _WINDOW:
            dq.popleft()
        if len(dq) >= rpm:
            retry = max(1, int(_WINDOW - (now - dq[0])))
            raise LimitError(
                f"Limite de {rpm} requisições por minuto atingido.",
                code="rate_limit_exceeded", retry_after=retry,
            )
        dq.append(now)


def acquire_slot(key: Any) -> bool:
    """Reserva uma vaga de concorrência. False = já há chamadas demais em voo."""
    limit = _limit(key, "concurrency")
    if not limit:
        return True
    kid = str(key.id)
    with _lock:
        if _inflight[kid] >= limit:
            return False
        _inflight[kid] += 1
    return True


def release_slot(key: Any) -> None:
    kid = str(key.id)
    with _lock:
        if _inflight.get(kid):
            _inflight[kid] -= 1
            if not _inflight[kid]:
                _inflight.pop(kid, None)


def inflight(key: Any) -> int:
    return _inflight.get(str(key.id), 0)


# chave -> instante da última gravação de `last_used_at`
_touched: dict[str, float] = {}
_TOUCH_EVERY = 60.0


def should_touch(key: Any, *, every: float = _TOUCH_EVERY) -> bool:
    """True no máximo uma vez por minuto por chave.

    `last_used_at`/`last_used_ip` são informativos (o painel mostra "último uso"),
    mas gravá-los a CADA requisição custava um UPDATE+COMMIT na MESMA linha da
    chave: sob concorrência, todas as chamadas daquela chave disputavam o mesmo
    lock de linha e serializavam. Uma amostra por minuto mantém o painel útil sem
    pôr uma escrita no caminho quente.
    """
    kid = str(key.id)
    now = time.time()
    with _lock:
        if now - _touched.get(kid, 0.0) < every:
            return False
        _touched[kid] = now
        if len(_touched) > _MAX_KEYS:  # poda o que já passou da janela
            for k in [k for k, t in _touched.items() if now - t >= every]:
                _touched.pop(k, None)
        return True


def reset() -> None:
    """Zera o estado em memória — usado pelos testes."""
    with _lock:
        _hits.clear()
        _inflight.clear()
        _touched.clear()


def _day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start() -> datetime:
    return _day_start().replace(day=1)


async def usage_window(db: AsyncSession, key: Any) -> dict[str, Any]:
    """Consumo da chave nos recortes que importam para os limites (dia e mês).

    Uma query agrega os dois períodos com `filter`, em vez de duas viagens ao
    banco — isto roda ANTES de cada chamada de modelo.
    """
    day, month = _day_start(), _month_start()
    row = (
        await db.execute(
            select(
                func.count().filter(ApiRequest.created_at >= day),
                func.count().filter(ApiRequest.created_at >= month),
                func.coalesce(
                    func.sum(ApiRequest.prompt_tokens).filter(ApiRequest.created_at >= month), 0
                ),
                func.coalesce(
                    func.sum(ApiRequest.completion_tokens).filter(ApiRequest.created_at >= month), 0
                ),
                func.coalesce(
                    func.sum(ApiRequest.cost).filter(ApiRequest.created_at >= month), 0.0
                ),
            ).where(ApiRequest.api_key_id == key.id, ApiRequest.created_at >= month)
        )
    ).one()
    return {
        "requests_today": int(row[0] or 0),
        "requests_month": int(row[1] or 0),
        "tokens_in_month": int(row[2] or 0),
        "tokens_out_month": int(row[3] or 0),
        "cost_month": float(row[4] or 0.0),
    }


async def check_quotas(db: AsyncSession, key: Any) -> dict[str, Any]:
    """Cotas persistentes (dia/mês/tokens/US$). Levanta LimitError ao estourar.

    Devolve a janela de uso para quem quiser reaproveitá-la (evita recontar).
    """
    win = await usage_window(db, key)

    rpd = _limit(key, "rpd")
    if rpd and win["requests_today"] >= rpd:
        tomorrow = _day_start() + timedelta(days=1)
        retry = max(1, int((tomorrow - datetime.now(timezone.utc)).total_seconds()))
        raise LimitError(
            f"Limite diário de {rpd} requisições atingido.",
            code="daily_quota_exceeded", retry_after=retry,
        )

    monthly = _limit(key, "monthly_requests")
    if monthly and win["requests_month"] >= monthly:
        raise LimitError(
            f"Limite mensal de {monthly} requisições atingido.",
            code="monthly_quota_exceeded",
        )

    tin = _limit(key, "tokens_in")
    if tin and win["tokens_in_month"] >= tin:
        raise LimitError(
            f"Limite mensal de {tin} tokens de entrada atingido.",
            code="input_token_quota_exceeded",
        )

    tout = _limit(key, "tokens_out")
    if tout and win["tokens_out_month"] >= tout:
        raise LimitError(
            f"Limite mensal de {tout} tokens de saída atingido.",
            code="output_token_quota_exceeded",
        )

    cap = _budget(key)
    if cap and win["cost_month"] >= cap:
        raise LimitError(
            f"Orçamento mensal de US$ {cap:.2f} atingido (gasto: US$ {win['cost_month']:.2f}).",
            code="budget_exceeded", status=402,
        )
    return win


def budget_alerts(key: Any, cost_month: float) -> list[int]:
    """Marcos de consumo (50%/80%/100%) recém-cruzados neste mês.

    `alerts_sent` é zerado quando o mês vira (o consumo despenca abaixo do menor
    marco), então o alerta volta a valer no ciclo seguinte sem cron nenhum.
    """
    cap = _budget(key)
    if not cap:
        return []
    pct = (cost_month / cap) * 100
    already = {int(x) for x in (key.alerts_sent or []) if isinstance(x, (int, float))}
    return [m for m in (50, 80, 100) if pct >= m and m not in already]


async def month_cost_by_key(db: AsyncSession, user_id) -> dict[str, float]:
    """Custo do mês por chave, vindo do ledger — é o "consumo por aplicação"."""
    rows = await db.execute(
        select(UsageEvent.api_key_id, func.coalesce(func.sum(UsageEvent.cost), 0.0))
        .where(UsageEvent.user_id == user_id, UsageEvent.created_at >= _month_start())
        .group_by(UsageEvent.api_key_id)
    )
    return {str(k): float(v or 0.0) for k, v in rows.all() if k}
