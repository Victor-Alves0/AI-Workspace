"""Assistente de primeiro uso contra o Postgres real: instalação vazia pede o setup, o
setup cria o admin uma vez só e a política de cadastro escolhida passa a valer."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


@pytest.fixture
def client(banco, engine, monkeypatch):
    from aiworkspace import audit_service, main
    from aiworkspace.db import get_db

    migrar(engine, "head")
    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    fabrica = async_sessionmaker(eng, expire_on_commit=False)

    async def _db():
        async with fabrica() as s:
            yield s

    async def _sem_auditoria(*_a, **_kw):
        return None

    monkeypatch.setattr(audit_service, "record", _sem_auditoria)
    app = main.create_app()
    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _setup(client, **extra):
    return client.post("/auth/setup", json={"name": "  Victor   Alves ", "email": "Dono@Casa.local",
                                            "password": "Senha-forte-1", **extra})


def test_primeiro_uso_cria_o_admin_uma_vez(client):
    assert client.get("/auth/config").json() == {"allow_signups": False, "needs_setup": True}

    r = _setup(client)
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["role"] == "admin" and u["status"] == "active" and u["email"] == "dono@casa.local"
    assert u["profile"]["name"] == "Victor Alves"
    assert "aiw_access" in r.cookies or client.cookies  # já entra logado

    assert client.get("/auth/config").json() == {"allow_signups": False, "needs_setup": False}
    assert _setup(client, email="outro@casa.local").status_code == 409
    # cadastro fechado (padrão do assistente)
    assert client.post("/auth/register", json={"email": "x@casa.local", "password": "Senha-forte-1"}).status_code == 403


def test_setup_com_cadastro_aberto(client):
    assert _setup(client, allow_signups=True).status_code == 200
    assert client.get("/auth/config").json()["allow_signups"] is True
    r = client.post("/auth/register", json={"email": "x@casa.local", "password": "Senha-forte-1"})
    assert r.status_code == 200 and r.json()["status"] == "pending"  # entra pendente de aprovação
