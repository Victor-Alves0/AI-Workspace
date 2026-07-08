"""Observabilidade leve em memória para o painel de debug.

- RingLogHandler: guarda os últimos N registros de log (sem dependência externa).
- RequestMetrics: contadores e tempos por endpoint + buffer de requests recentes.

Tudo fica só em memória do processo (zera no restart) — suficiente para diagnosticar
e alimentar o painel /debug, sem persistir dados sensíveis.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock
from typing import Any


class RingLogHandler(logging.Handler):
    """Mantém os últimos `capacity` logs em um buffer circular."""

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["exc"] = self.formatException(record.exc_info)[-2000:]
            with self._lock:
                self._buf.append(entry)
        except Exception:  # noqa: BLE001
            pass  # logging nunca deve quebrar a aplicação

    def recent(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        if level:
            wanted = level.upper()
            items = [e for e in items if e["level"] == wanted]
        return items[-limit:]


class RequestMetrics:
    def __init__(self, capacity: int = 200) -> None:
        self.total = 0
        self.errors = 0
        self.by_route: dict[str, dict[str, float]] = {}
        self.recent: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = Lock()
        self.started_at = time.time()

    def record(self, method: str, path: str, status: int, ms: float) -> None:
        with self._lock:
            self.total += 1
            if status >= 500:
                self.errors += 1
            key = f"{method} {path}"
            # evita crescimento ilimitado com paths que carregam IDs
            if key not in self.by_route and len(self.by_route) >= 500:
                key = f"{method} <outros>"
            agg = self.by_route.setdefault(key, {"count": 0, "total_ms": 0.0, "errors": 0})
            agg["count"] += 1
            agg["total_ms"] += ms
            if status >= 500:
                agg["errors"] += 1
            self.recent.append(
                {"ts": time.time(), "method": method, "path": path, "status": status, "ms": round(ms, 1)}
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = [
                {
                    "route": k,
                    "count": v["count"],
                    "avg_ms": round(v["total_ms"] / v["count"], 1) if v["count"] else 0,
                    "errors": v["errors"],
                }
                for k, v in sorted(self.by_route.items(), key=lambda i: -i[1]["count"])
            ]
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "total_requests": self.total,
                "error_requests": self.errors,
                "routes": routes[:50],
                "recent": list(self.recent)[-50:],
            }


# instâncias globais do processo
ring_handler = RingLogHandler()
metrics = RequestMetrics()


def install_logging() -> None:
    """Anexa o ring handler ao logger raiz (uma vez)."""
    root = logging.getLogger()
    if not any(isinstance(h, RingLogHandler) for h in root.handlers):
        ring_handler.setLevel(logging.INFO)
        root.addHandler(ring_handler)
