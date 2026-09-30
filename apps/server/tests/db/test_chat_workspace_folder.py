"""Pasta de trabalho do chat (seletor de pastas) contra o Postgres real: o "espaço do
chat" OCULTO antigo vira pasta visível ligada ao chat (sem apagar nada), a pasta
principal é o padrão, escolher/“sem pasta” muda o que a IA alcança, e o navegador de
pastas respeita os limites."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


def test_migracao_torna_o_espaco_oculto_visivel_e_ligado_ao_chat(engine):
    migrar(engine, "0084_obs_analysis_indexes")
    uid, cid, pid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, email, hashed_password, role, is_active, status, profile, token_version, totp_enabled, created_at, updated_at) "
                       "VALUES (:u, 'a@b.c', 'x', 'admin', true, 'active', '{}', 0, false, now(), now())"), {"u": uid})
        c.execute(text("INSERT INTO chats (id, user_id, title, model, created_at, updated_at) "
                       "VALUES (:c, :u, 'Bomberman', 'm', now(), now())"), {"c": cid, "u": uid})
        c.execute(text(
            "INSERT INTO codespace_projects (id, user_id, name, source, repo_url, branch, scope, index_status, created_at, updated_at) "
            "VALUES (:p, :u, 'Espaço do chat: Bomberman', 'local', '', 'main', CAST(:s AS jsonb), 'ready', now(), now())"),
            {"p": pid, "u": uid, "s": f'{{"chat_workspace": "{cid}"}}'})
    migrar(engine, "head")
    with engine.begin() as c:
        ws = c.execute(text("SELECT workspace FROM chats WHERE id = :c"), {"c": cid}).scalar()
        nome, scope = c.execute(text("SELECT name, scope FROM codespace_projects WHERE id = :p"), {"p": pid}).one()
    assert ws == str(pid)
    assert nome == "Bomberman"
    assert "chat_workspace" not in scope and scope["from_chat"] == str(cid)


@pytest.fixture
def cliente(banco, engine, monkeypatch, tmp_path):
    from aiworkspace import audit_service, db as dbmod, main
    from aiworkspace.config import get_settings

    migrar(engine, "head")
    monkeypatch.setattr(get_settings(), "workspace_home", str(tmp_path / "homes" / "{user}"))
    monkeypatch.setattr(get_settings(), "database_url", banco.replace("postgresql://", "postgresql+asyncpg://", 1))
    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    fabrica = async_sessionmaker(eng, expire_on_commit=False)

    async def _db():
        async with fabrica() as s:
            yield s

    async def _sem_auditoria(*_a, **_kw):
        return None

    monkeypatch.setattr(audit_service, "record", _sem_auditoria)
    app = main.create_app()
    app.dependency_overrides[dbmod.get_db] = _db
    c = TestClient(app)
    assert c.post("/auth/setup", json={"name": "Dono", "email": "dono@casa.local",
                                       "password": "Senha-forte-1"}).status_code == 200
    return c, fabrica, tmp_path


def test_pasta_principal_escolha_e_sem_pasta(cliente, monkeypatch):
    from aiworkspace.chat import turn_setup
    from aiworkspace.codespace import graph_service
    from aiworkspace.models import Chat

    c, fabrica, tmp = cliente
    monkeypatch.setattr(graph_service, "_index_project", lambda *a, **k: asyncio.sleep(0))

    pastas = c.get("/workspace/folders").json()
    home = pastas["home"]
    assert home["name"] == "Pasta principal" and home["path"].startswith(str(tmp / "homes"))
    assert pastas["browse_anywhere"] is True                  # dono da instância

    r = c.post("/chats", json={"title": "x"})
    assert r.status_code == 200, r.text
    chat = r.json()
    assert chat["workspace"] is None
    uid = c.get("/auth/me").json()["id"]
    pid = asyncio.run(graph_service._ensure_chat_workspace(uid, chat["id"]))
    assert pid == home["id"]                                  # padrão: a principal

    # nova pasta dentro da principal, criada pelo seletor
    nova = c.post("/workspace/folders", json={"path": "jogos/bomberman", "create": True}).json()
    assert nova["path"].endswith("bomberman") and (tmp / "homes").exists()
    assert c.patch(f"/chats/{chat['id']}", json={"workspace": nova["id"]}).json()["workspace"] == nova["id"]
    assert asyncio.run(graph_service._ensure_chat_workspace(uid, chat["id"])) == nova["id"]

    # sem pasta: nada de ferramentas de arquivo
    assert c.patch(f"/chats/{chat['id']}", json={"workspace": "off"}).json()["workspace"] == "off"
    assert asyncio.run(graph_service._ensure_chat_workspace(uid, chat["id"])) is None

    async def _ws_on():
        async with fabrica() as s:
            ch = await s.get(Chat, uuid.UUID(chat["id"]))
            mc = type("MC", (), {"tools_enabled": True, "capabilities": {}})()
            return turn_setup._workspace_on(ch, mc)

    assert asyncio.run(_ws_on()) is False
    # de volta à principal
    assert c.patch(f"/chats/{chat['id']}", json={"workspace": "home"}).json()["workspace"] is None
    assert asyncio.run(_ws_on()) is True

    # id de pasta de outro usuário/inexistente: recusado, a pasta do chat não muda
    assert c.patch(f"/chats/{chat['id']}", json={"workspace": str(uuid.uuid4())}).status_code in (403, 404)

    nav = c.get("/workspace/browse").json()
    assert [d["name"] for d in nav["dirs"]] == ["jogos"]
