"""Login local do CLI `aiw` (POST /auth/local) contra o Postgres real: só no motor
desktop, só do loopback, só com o token do arquivo — e entra como o dono (1º admin)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401

TOKEN = "token-local-de-teste-0123456789abcdef"
HDR = {"x-aiw-local-token": TOKEN}


@pytest.fixture
def app(banco, engine, monkeypatch):
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
    a = main.create_app()
    a.dependency_overrides[get_db] = _db
    return a


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    f = tmp_path / "cli-token"
    f.write_text(TOKEN)
    monkeypatch.setenv("AIW_LOCAL_TOKEN_FILE", str(f))
    return f


def _local(app) -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def test_fora_do_motor_desktop_a_rota_nao_existe(app, monkeypatch):
    monkeypatch.delenv("AIW_LOCAL_TOKEN_FILE", raising=False)
    assert _local(app).post("/auth/local", headers=HDR).status_code == 404


def test_so_do_loopback(app, token_file):
    remoto = TestClient(app, client=("192.168.0.20", 50000))
    assert remoto.post("/auth/local", headers=HDR).status_code == 403


def test_token_errado_ou_ausente(app, token_file):
    c = _local(app)
    assert c.post("/auth/local", headers={"x-aiw-local-token": "outro"}).status_code == 401
    assert c.post("/auth/local").status_code == 401


def test_sem_conta_pede_o_setup(app, token_file):
    r = _local(app).post("/auth/local", headers=HDR)
    assert r.status_code == 409 and r.json()["detail"] == "needs_setup"


def test_entra_como_o_dono(app, token_file):
    c = _local(app)
    dono = c.post("/auth/setup", json={"name": "Dono", "email": "dono@casa.local",
                                       "password": "Senha-forte-1", "allow_signups": True})
    assert dono.status_code == 200, dono.text
    # um 2º usuário (aprovado ou não) não muda quem o token local representa
    c.post("/auth/register", json={"email": "outro@casa.local", "password": "Senha-forte-1"})

    novo = _local(app)  # sem cookies
    r = novo.post("/auth/local", headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "dono@casa.local"
    assert novo.get("/auth/me").json()["email"] == "dono@casa.local"  # a sessão vale na API
