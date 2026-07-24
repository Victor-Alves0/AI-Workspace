"""Fila em memória das chamadas assíncronas (`background: true`).

O cliente dispara e recebe um id; depois busca o resultado em
``GET /v1/chat/completions/{id}``. Serve para requisições longas (agente com muitas
ferramentas) em ambientes que derrubam conexões ociosas — serverless, webhooks,
integrações no-code.

O estado vive em MEMÓRIA, com TTL: um resultado de completion é efêmero por
natureza e o cliente busca em segundos. Persistir no banco custaria uma tabela e
uma migração para dados que ninguém lê depois. O contrato assumido: um restart do
servidor perde jobs em voo, e o cliente vê 404 — mesma coisa que um timeout.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

_TTL = 3600.0      # 1h para buscar o resultado
_MAX_JOBS = 500    # teto de segurança; os mais velhos saem primeiro

_jobs: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


def _purge(now: float) -> None:
    dead = [k for k, j in _jobs.items() if now - j["created"] > _TTL]
    for k in dead:
        _jobs.pop(k, None)
    while len(_jobs) > _MAX_JOBS:
        oldest = min(_jobs, key=lambda k: _jobs[k]["created"])
        _jobs.pop(oldest, None)


async def create(user_id: str, key_id: str, model: str) -> str:
    job_id = f"job-{uuid.uuid4().hex}"
    now = time.time()
    async with _lock:
        _purge(now)
        _jobs[job_id] = {
            "id": job_id, "status": "queued", "created": now,
            "user_id": str(user_id), "key_id": str(key_id), "model": model,
            "result": None, "error": None,
        }
    return job_id


async def update(job_id: str, **fields: Any) -> None:
    async with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)


async def get(job_id: str, user_id: str) -> dict[str, Any] | None:
    """Job do usuário. Um id de outra conta responde None (vira 404) — ids são
    aleatórios, mas a checagem de dono é o que garante o isolamento."""
    async with _lock:
        job = _jobs.get(job_id)
        if job is None or job["user_id"] != str(user_id):
            return None
        return dict(job)


async def clear() -> None:
    async with _lock:
        _jobs.clear()
