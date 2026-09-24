"""Ambiente do Alembic. Online: asyncpg (o único driver do app); a bateria de
testes injeta uma conexão síncrona própria."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
import asyncio

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from aiworkspace import pgsync
from aiworkspace.config import get_settings
from aiworkspace.db import Base
import aiworkspace.models  # noqa: F401  (registra todas as tabelas no metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # conexão injetada (bateria de testes de banco): migra o banco descartável do
    # teste, não o do DATABASE_URL — e dentro da transação que o teste controla
    injected = config.attributes.get("connection")
    if injected is not None:
        context.configure(connection=injected, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    asyncio.run(_run_async())


def _migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(pgsync.sqlalchemy_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
