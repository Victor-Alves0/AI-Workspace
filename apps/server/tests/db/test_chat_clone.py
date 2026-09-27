"""Duplicar / fork de conversa contra o Postgres real: a cópia mantém a ORDEM das
mensagens (created_at original — antes todas ganhavam o mesmo now()), herda as
configurações, deixa de fora os resumos de compactação e solta os anexos do chat de
origem (apagar o original não leva os arquivos do fork junto)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
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
    c = TestClient(app)
    r = c.post("/auth/setup", json={"name": "Dono", "email": "dono@casa.local", "password": "Senha-forte-1"})
    assert r.status_code == 200, r.text
    return c


def _semear(engine, chat_id: str, user_id: str) -> str:
    """5 mensagens com horários crescentes + 1 resumo de compactação + 1 anexo."""
    up = str(uuid.uuid4())
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO uploads (id, user_id, chat_id, filename, mime, size, kind, path, attached, created_at, updated_at) "
            "VALUES (:id, :u, :c, 'a.txt', 'text/plain', 1, 'file', 'x/a.txt', true, now(), now())"),
            {"id": up, "u": user_id, "c": chat_id})
        linhas = [("user", "um", False), ("assistant", "dois", False), ("user", "três", False),
                  ("assistant", "RESUMO", True), ("assistant", "quatro", False)]
        for i, (role, conteudo, resumo) in enumerate(linhas):
            anexos = f'[{{"type": "file", "name": "a.txt", "upload_id": "{up}"}}]' if i == 0 else None
            c.execute(text(
                "INSERT INTO messages (id, chat_id, role, content, is_summary, compacted, attachments, created_at, updated_at) "
                "VALUES (:id, :c, :r, :t, :s, false, CAST(:a AS jsonb), :ts, :ts)"),
                {"id": str(uuid.uuid4()), "c": chat_id, "r": role, "t": conteudo, "s": resumo,
                 "a": anexos, "ts": t0 + timedelta(minutes=i)})
    return up


def test_clone_mantem_ordem_config_e_solta_anexos(client, engine):
    me = client.get("/auth/me").json()
    chat = client.post("/chats", json={"title": "Original", "model": "x/y",
                                       "params": {"temperature": 0.3}}).json()
    up = _semear(engine, chat["id"], me["id"])

    r = client.post(f"/chats/{chat['id']}/clone", json={"title": "Original (fork)"})
    assert r.status_code == 200, r.text
    fork = r.json()
    assert fork["title"] == "Original (fork)" and fork["model"] == "x/y"

    msgs = client.get(f"/chats/{fork['id']}/messages").json()
    assert [m["content"] for m in msgs] == ["um", "dois", "três", "quatro"]  # ordem + sem resumo

    # apagar o original não leva o anexo do fork junto
    assert client.delete(f"/chats/{chat['id']}").status_code in (200, 204)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM uploads WHERE id = :i"), {"i": up}).scalar_one() == 1


def test_clone_sem_corpo_continua_como_duplicar(client):
    chat = client.post("/chats", json={"title": "Conversa", "model": "x/y"}).json()
    r = client.post(f"/chats/{chat['id']}/clone")
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Conversa (cópia)"
