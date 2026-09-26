"""Resetar o sistema (Admin → Backup e migração) contra o Postgres real: senha errada
não apaga nada; senha certa apaga banco + anexos + Codespace e a instalação volta ao
primeiro uso (o assistente de setup reaparece)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


@pytest.fixture
def client(banco, engine, monkeypatch, tmp_path):
    from aiworkspace import admin_routes, audit_service, main
    from aiworkspace.db import get_db

    migrar(engine, "head")
    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    fabrica = async_sessionmaker(eng, expire_on_commit=False)

    async def _db():
        async with fabrica() as s:
            yield s

    async def _sem_auditoria(*_a, **_kw):
        return None

    async def _upgrade():
        # o subprocess real usaria o DATABASE_URL do processo; aqui migra o banco do teste
        engine.dispose()
        migrar(engine, "head")
        return 0, ""

    uploads, cs = tmp_path / "uploads", tmp_path / "codespace"
    for pasta in (uploads, cs):
        (pasta / "sub").mkdir(parents=True)
        (pasta / "sub" / "a.txt").write_text("x")
        (pasta / "b.txt").write_text("y")
    monkeypatch.setattr(audit_service, "record", _sem_auditoria)
    monkeypatch.setattr(admin_routes, "_alembic_upgrade", _upgrade)
    monkeypatch.setattr(admin_routes, "_uploads_root", lambda: uploads)
    monkeypatch.setattr(admin_routes, "_codespace_root", lambda: cs)
    app = main.create_app()
    app.dependency_overrides[get_db] = _db
    c = TestClient(app)
    c.pastas = (uploads, cs)  # type: ignore[attr-defined]
    r = c.post("/auth/setup", json={"name": "Dono", "email": "dono@casa.local", "password": "Senha-forte-1"})
    assert r.status_code == 200, r.text
    return c


def _usuarios(engine) -> int:
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM users")).scalar_one()


def test_senha_errada_nao_apaga_nada(client, engine):
    r = client.post("/admin/reset", json={"password": "errada-123"})
    assert r.status_code == 403
    assert _usuarios(engine) == 1
    assert all(any(p.iterdir()) for p in client.pastas)
    assert client.get("/auth/config").json()["needs_setup"] is False


def test_reset_apaga_tudo_e_volta_ao_primeiro_uso(client, engine):
    r = client.post("/admin/reset", json={"password": "Senha-forte-1"})
    assert r.status_code == 200, r.text
    assert _usuarios(engine) == 0
    # as pastas continuam existindo, vazias
    assert all(p.is_dir() and not any(p.iterdir()) for p in client.pastas)
    assert client.get("/auth/config").json() == {"allow_signups": False, "needs_setup": True}
    # e o setup funciona de novo no esquema recriado
    r = client.post("/auth/setup", json={"name": "Novo", "email": "novo@casa.local", "password": "Senha-forte-2"})
    assert r.status_code == 200, r.text


def test_reset_exige_admin(client):
    client.cookies.clear()
    assert client.post("/admin/reset", json={"password": "Senha-forte-1"}).status_code in (401, 403)
