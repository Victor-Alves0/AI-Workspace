"""Agentes em segundo plano sobrevivem a um restart (Postgres real): o trabalho que
rodava é retomado só com os membros sem checkpoint; o que terminou sem acordar o chat
é entregue; depois de muitas quedas seguidas, desiste e avisa."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


@pytest.fixture
def ambiente(banco, engine, monkeypatch):
    from aiworkspace import audit_service, db as dbmod, main
    from aiworkspace.chat import subagent_jobs

    migrar(engine, "head")
    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    fabrica = async_sessionmaker(eng, expire_on_commit=False)

    async def _db():
        async with fabrica() as s:
            yield s

    async def _sem_auditoria(*_a, **_kw):
        return None

    monkeypatch.setattr(audit_service, "record", _sem_auditoria)
    monkeypatch.setattr(dbmod, "SessionLocal", fabrica)
    app = main.create_app()
    app.dependency_overrides[dbmod.get_db] = _db
    c = TestClient(app)
    assert c.post("/auth/setup", json={"name": "Dono", "email": "dono@casa.local",
                                       "password": "Senha-forte-1"}).status_code == 200
    chat = c.post("/chats", json={"title": "Ondas"}).json()
    with engine.begin() as cx:
        user_id = cx.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
    subagent_jobs._jobs.clear()
    subagent_jobs._pending.clear()
    subagent_jobs._patches.clear()
    yield chat["id"], str(user_id)
    subagent_jobs._jobs.clear()
    subagent_jobs._pending.clear()
    subagent_jobs._patches.clear()


def _job(engine, chat_id, user_id, key, *, status="running", attempts=1, results=None, note=None, card=None):
    spec = {"kind": "team", "runner": {}, "team": {"goal": "g", "label": "Wave", "chain": False,
            "members": [{"name": f"M{i}", "task": f"t{i}", "instructions": "", "isolated": False}
                        for i in range(3)]}}
    with engine.begin() as cx:
        cx.execute(text(
            "INSERT INTO subagent_jobs (id, job_key, chat_id, user_id, kind, name, task, spec, results, "
            "status, delivered, attempts, note, card) VALUES (:id, :k, :c, :u, 'team', 'Wave', 'g', "
            "CAST(:s AS jsonb), CAST(:r AS jsonb), :st, false, :a, :n, CAST(:cd AS jsonb))"),
            {"id": str(uuid.uuid4()), "k": key, "c": chat_id, "u": user_id, "s": json.dumps(spec),
             "r": json.dumps(results or {}), "st": status, "a": attempts, "n": note,
             "cd": json.dumps(card) if card is not None else None})


def test_retoma_so_os_membros_que_faltavam(ambiente, engine, monkeypatch):
    from aiworkspace.chat import subagent_jobs, turn_setup

    chat_id, user_id = ambiente
    _job(engine, chat_id, user_id, "onda1", results={"0": {"output": "feito antes"}})
    _job(engine, chat_id, user_id, "onda2", status="done", note="[Agente em segundo plano concluído] Wave",
         card={"kind": "subagent_team", "members": []})
    _job(engine, chat_id, user_id, "onda3", attempts=3)

    chamadas = []

    async def fake_run(jid, uid, cid, spec, done=None):
        chamadas.append((jid, sorted((done or {}).keys())))
        return {"output": "ok", "card": {"kind": "subagent_team"}}

    entregas = []
    monkeypatch.setattr(turn_setup, "run_background_spec", fake_run)
    monkeypatch.setattr(subagent_jobs, "_deliver", lambda c, n, j=None: entregas.append((j, n[:40])))

    async def go():
        await subagent_jobs.recover()
        for _ in range(50):
            await asyncio.sleep(0.02)

    asyncio.run(go())
    assert chamadas == [("onda1", [0])]
    jobs = {j for j, _ in entregas}
    assert {"onda1", "onda2", "onda3"} <= jobs
    with engine.begin() as cx:
        linhas = dict(cx.execute(text("SELECT job_key, status || ':' || attempts FROM subagent_jobs")).all())
    assert linhas["onda1"] == "done:2"
    assert linhas["onda3"].startswith("failed")


def test_checkpoint_acumula_por_membro(ambiente, engine):
    from aiworkspace.chat import subagent_jobs

    chat_id, user_id = ambiente
    _job(engine, chat_id, user_id, "ck")

    async def go():
        await subagent_jobs._db_checkpoint("ck", 1, {"output": "um"})
        await subagent_jobs._db_checkpoint("ck", 2, {"output": "dois"})

    asyncio.run(go())
    with engine.begin() as cx:
        res = cx.execute(text("SELECT results FROM subagent_jobs WHERE job_key='ck'")).scalar_one()
    assert res == {"1": {"output": "um"}, "2": {"output": "dois"}}
