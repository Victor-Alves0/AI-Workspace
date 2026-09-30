"""Um processo servidor por banco (single_instance.py), contra o Postgres real: o
segundo processo espera e depois se RECUSA a subir; quando o primeiro sai, sobe."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import pytestmark  # noqa: F401


def test_second_process_refuses_then_takes_over(banco):
    from aiworkspace import single_instance as si

    url = banco.replace("postgresql://", "postgresql+asyncpg://", 1)

    async def go():
        a = create_async_engine(url, poolclass=NullPool)
        b = create_async_engine(url, poolclass=NullPool)
        try:
            assert await si.acquire(a) is True
            primeiro, si._conn = si._conn, None          # "outro processo"
            with pytest.raises(si.AlreadyRunning):
                await si.acquire(b, wait_seconds=1.2, poll=0.3)
            # o primeiro sai (deploy/restart): a conexão fecha e o lock é solto
            si._conn = primeiro
            await si.release()
            assert await si.acquire(b, wait_seconds=2) is True
            await si.release()
        finally:
            await a.dispose()
            await b.dispose()

    asyncio.run(go())


def test_waits_for_a_process_that_is_shutting_down(banco):
    from aiworkspace import single_instance as si

    url = banco.replace("postgresql://", "postgresql+asyncpg://", 1)

    async def go():
        a = create_async_engine(url, poolclass=NullPool)
        b = create_async_engine(url, poolclass=NullPool)
        try:
            await si.acquire(a)
            velho, si._conn = si._conn, None

            async def sai_logo():
                await asyncio.sleep(0.8)
                await velho.close()                       # processo antigo terminando

            t = asyncio.create_task(sai_logo())
            assert await si.acquire(b, wait_seconds=5, poll=0.2) is True
            await t
            await si.release()
        finally:
            await a.dispose()
            await b.dispose()

    asyncio.run(go())
