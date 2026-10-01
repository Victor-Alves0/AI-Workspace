"""Caso CyberLab no banco real: criação idempotente e o loop gravando de verdade.

Prova que o que o tool devolve é o que ficou no caso: salvar escopo sai do scoping
para recon, cada `step` acumula no log, `finding` acumula nos achados, e abrir o caso
duas vezes no mesmo chat não cria um segundo."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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
