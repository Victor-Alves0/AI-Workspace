"""Rate limiting em memória (janela deslizante) para endpoints sensíveis."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from ..config import get_settings

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()
# teto de chaves rastreadas: evita crescimento sem limite se alguém pulverizar
# e-mails/IPs falsos; ao atingir, entradas expiradas são purgadas primeiro
_MAX_KEYS = 10_000


def _client_ip(request: Request) -> str:
    # X-Forwarded-For só é confiável atrás de um proxy reverso controlado; sem
    # proxy, qualquer cliente pode forjá-lo e contornar o rate limit. Por isso
    # só é considerado quando TRUST_PROXY=true no ambiente.
    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _purge_expired(now: float, window: float) -> None:
    """Remove chaves sem tentativas dentro da janela (chamar com _lock)."""
    dead = [k for k, dq in _hits.items() if not dq or now - dq[-1] > window]
    for k in dead:
        del _hits[k]


def check_login_rate(request: Request, identifier: str = "") -> None:
    """Levanta 429 se houver tentativas demais por IP+identificador na janela."""
    s = get_settings()
    key = f"{_client_ip(request)}:{identifier.lower()}"
    now = time.time()
    window = s.login_window_seconds
    with _lock:
        if len(_hits) >= _MAX_KEYS and key not in _hits:
            _purge_expired(now, window)
        dq = _hits[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= s.login_max_attempts:
            retry = int(window - (now - dq[0]))
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Muitas tentativas. Tente novamente em {max(retry, 1)}s.",
            )
        dq.append(now)
