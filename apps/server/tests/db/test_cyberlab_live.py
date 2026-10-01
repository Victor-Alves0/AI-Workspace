"""Caso CyberLab no banco real: criação idempotente e o loop gravando de verdade.

Prova que o que o tool devolve é o que ficou no caso: salvar escopo sai do scoping
para recon, cada `step` acumula no log, `finding` acumula nos achados, e abrir o caso
duas vezes no mesmo chat não cria um segundo."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


def _async(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _fluxo(url: str) -> dict:
    from aiworkspace.cyberlab import service
    from aiworkspace.cyberlab.turns import apply_action
    from aiworkspace.models import Chat, CyberLabCase, User

    eng = create_async_engine(_async(url))
    out: dict = {}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
            db.add(user)
            await db.flush()
            chat = Chat(user_id=user.id, title="t", mini_app="cyberlab")
            db.add(chat)
            await db.commit()

            case = await service.create_case(db, user.id, chat.id)
            again = await service.create_case(db, user.id, chat.id)  # idempotente
            out["mesmo_caso"] = str(case.id) == str(again.id)
            out["fase_inicial"] = case.phase

            out["scope"] = await apply_action(db, user.id, chat.id, {
                "action": "scope", "target": "10.0.0.5",
                "authorization": "lab próprio", "objective": "mapear",
            })
            out["step1"] = await apply_action(db, user.id, chat.id, {
                "action": "step", "command": "nmap -sV 10.0.0.5",
                "output": "22/tcp open ssh OpenSSH 8.2", "note": "ssh exposto",
            })
            out["step2"] = await apply_action(db, user.id, chat.id, {
                "action": "step", "command": "curl -I http://10.0.0.5",
                "output": "Server: nginx/1.18.0", "note": "web nginx",
            })
            out["finding"] = await apply_action(db, user.id, chat.id, {
                "action": "finding", "title": "OpenSSH desatualizado",
                "severity": "medium", "detail": "8.2", "refs": ["CVE-0000-0000"],
            })
            out["finding_sem_titulo"] = await apply_action(db, user.id, chat.id, {"action": "finding"})

            fresh = await db.get(CyberLabCase, case.id)
            out["gravado_target"] = fresh.target
            out["gravado_auth"] = fresh.authorization
            out["gravado_log"] = len(fresh.settings["log"])
            out["gravado_findings"] = len(fresh.settings["findings"])
        return out
    finally:
        await eng.dispose()


async def _autofuzz(url: str) -> dict:
    from aiworkspace.cyberlab import service
    from aiworkspace.cyberlab.turns import apply_action
    from aiworkspace.models import Chat, CyberLabCase, User

    eng = create_async_engine(_async(url))
    out: dict = {}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
            db.add(user)
            await db.flush()
            chat = Chat(user_id=user.id, title="t", mini_app="cyberlab")
            db.add(chat)
            await db.commit()

            case = await service.create_case(db, user.id, chat.id, mode="autofuzz")
            out["mode"] = case.mode
            # salvar escopo no autofuzz vai para 'harness' (não 'recon')
            out["scope"] = await apply_action(db, user.id, chat.id, {
                "action": "scope", "target": "libfoo (projeto Codespace)",
                "authorization": "repo próprio", "objective": "achar OOB no parser",
            })
            # fase válida do autofuzz
            out["fuzz"] = await apply_action(db, user.id, chat.id, {"action": "phase", "phase": "fuzz"})
            # fase de OUTRO modo é recusada (recon é do blackbox)
            out["fase_invalida"] = await apply_action(db, user.id, chat.id, {"action": "phase", "phase": "recon"})
            # finding com crash + repro
            out["finding"] = await apply_action(db, user.id, chat.id, {
                "action": "finding", "title": "heap OOB em parse_header",
                "severity": "high", "crash": "heap-buffer-overflow READ",
                "repro": "poc.bin (base64 …) | ./fuzz_parser poc.bin",
                "detail": "lê 1 byte além do buffer quante len=0",
            })
            fresh = await db.get(CyberLabCase, case.id)
            out["gravado"] = fresh.settings["findings"][0]
        return out
    finally:
        await eng.dispose()


def test_fluxo_do_caso_cyberlab(banco, engine):  # noqa: F811
    migrar(engine, "head")
    r = asyncio.run(_fluxo(banco))
    assert r["mesmo_caso"] is True
    assert r["fase_inicial"] == "scoping"
    assert r["scope"]["phase"] == "recon"          # salvar escopo saiu do scoping
    assert r["step1"]["steps"] == 1 and r["step2"]["steps"] == 2
    assert r["finding"]["findings"][0]["refs"] == ["CVE-0000-0000"]
    assert "error" in r["finding_sem_titulo"]       # achado sem título é recusado
    assert r["gravado_target"] == "10.0.0.5" and r["gravado_auth"] == "lab próprio"
    assert r["gravado_log"] == 2 and r["gravado_findings"] == 1


def test_fluxo_autofuzz(banco, engine):  # noqa: F811
    migrar(engine, "head")
    r = asyncio.run(_autofuzz(banco))
    assert r["mode"] == "autofuzz"
    assert r["scope"]["phase"] == "harness"      # autofuzz: scoping → harness
    assert r["fuzz"]["phase"] == "fuzz"
    assert "error" in r["fase_invalida"]          # fase de outro modo é recusada
    assert r["gravado"]["crash"] == "heap-buffer-overflow READ"
    assert "poc.bin" in r["gravado"]["repro"]


@pytest.fixture
def cliente(banco, engine, monkeypatch, tmp_path):
    from aiworkspace import audit_service, db as dbmod, main
    from aiworkspace.config import get_settings

    migrar(engine, "head")
    monkeypatch.setattr(get_settings(), "workspace_home", str(tmp_path / "homes" / "{user}"))
    monkeypatch.setattr(get_settings(), "database_url", _async(banco))
    eng = create_async_engine(_async(banco), poolclass=NullPool)
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
    return c, fabrica


def test_endpoint_liga_cyberlab_no_chat(cliente):
    """POST /chats/{id}/cyberlab marca o Mini App e cria o caso no modo pedido — o
    seam que a UI (botão no chat de projeto) vai chamar."""
    c, fabrica = cliente
    chat = c.post("/chats", json={"title": "alvo", "model": "m"}).json()
    r = c.post(f"/chats/{chat['id']}/cyberlab", json={"mode": "autofuzz"})
    assert r.status_code == 200, r.text
    assert r.json()["mini_app"] == "cyberlab"

    async def _mode():
        from aiworkspace.cyberlab import service
        async with fabrica() as s:
            uid = uuid.UUID(c.get("/auth/me").json()["id"])
            case = await service.get_case(s, uid, uuid.UUID(chat["id"]))
            return case.mode

    assert asyncio.run(_mode()) == "autofuzz"
    # modo inválido é recusado pelo schema
    assert c.post(f"/chats/{chat['id']}/cyberlab", json={"mode": "x"}).status_code == 422
