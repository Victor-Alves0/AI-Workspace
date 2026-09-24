"""Acesso SÍNCRONO ao Postgres pelo asyncpg — o único driver do app.

Alguns caminhos são síncronos por natureza (memória e health rodam em threads; o
reaper dos jobs do Codespace; a rotação de chaves). Eles usavam o psycopg2, um
segundo driver só para isso. Aqui cada chamada abre uma conexão asyncpg num loop
PRÓPRIO: `asyncio.run` na thread chamadora, ou numa thread auxiliar quando já há um
loop rodando nela (chamar asyncio.run dentro de um loop é erro).

Placeholders no estilo do asyncpg: $1, $2… JSON/JSONB entram e saem como objetos
Python (codec registrado na conexão).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .config import get_settings

T = TypeVar("T")
_TIMEOUT = 10  # nunca pendurar: banco inacessível falha em 10s


def dsn() -> str:
    """URL libpq (sem o sufixo do driver do SQLAlchemy)."""
    url = get_settings().database_url
    for suf in ("+asyncpg", "+psycopg2", "+psycopg"):
        url = url.replace(suf, "")
    return url


def sqlalchemy_url(url: str | None = None) -> str:
    """URL para o `create_async_engine` do SQLAlchemy (dialeto asyncpg)."""
    base = url or dsn()
    for suf in ("+asyncpg", "+psycopg2", "+psycopg"):
        base = base.replace(suf, "")
    return base.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _connect():
    import asyncpg

    conn = await asyncpg.connect(dsn(), timeout=_TIMEOUT)
    for tipo in ("json", "jsonb"):
        await conn.set_type_codec(tipo, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    return conn


def run_sync(coro_fn: Callable[[], Awaitable[T]]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    # há um loop rodando nesta thread: executa num loop novo, em outra thread
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_fn())).result()


def with_conn(fn: Callable[[Any], Awaitable[T]], *, transaction: bool = True) -> T:
    """Roda `await fn(conn)` numa conexão própria (numa transação, por padrão)."""
    async def _go() -> T:
        conn = await _connect()
        try:
            if transaction:
                async with conn.transaction():
                    return await fn(conn)
            return await fn(conn)
        finally:
            await conn.close()
    return run_sync(_go)


def fetch(sql: str, *args: Any) -> list[Any]:
    """Linhas (asyncpg.Record: acessa por índice ou nome)."""
    return with_conn(lambda c: c.fetch(sql, *args), transaction=False)


def execute(sql: str, *args: Any) -> int:
    """Executa e devolve o nº de linhas afetadas (do status "UPDATE 3", "DELETE 1"…)."""
    status = with_conn(lambda c: c.execute(sql, *args))
    return affected(status)


def executemany(sql: str, args: list[tuple]) -> None:
    with_conn(lambda c: c.executemany(sql, args))


def from_pyformat(sql: str) -> str:
    """SQL no estilo do psycopg (`%s`, `%%`) → estilo do asyncpg (`$1`, `%`)."""
    out: list[str] = []
    n = 0
    i = 0
    while i < len(sql):
        if sql.startswith("%%", i):
            out.append("%")
            i += 2
        elif sql.startswith("%s", i):
            n += 1
            out.append(f"${n}")
            i += 2
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def affected(status: str) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0
