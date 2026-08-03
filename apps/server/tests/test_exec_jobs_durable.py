"""Durabilidade dos jobs de background do Codespace: a recuperação no boot destrava
o 'wake que nunca chega'. Um job órfão (settled=False de um processo anterior) é
marcado interrompido e o chat é acordado; um job DESTE processo (no _jobs) é poupado.

`_db_insert`/`_db_update` são fire-and-forget (nunca levantam). Fluxo de DB pula com
graça se o Postgres não estiver acessível."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from aiworkspace.codespace import exec_jobs as ej


# --------------------------------------------------------------------------- #
# Puro
# --------------------------------------------------------------------------- #
def test_persistencia_nunca_levanta_sem_db(monkeypatch):
    def boom():
        raise RuntimeError("db fora")
    monkeypatch.setattr(ej, "_pg", boom)
    ej._db_update("qualquer", status="done")           # não levanta
    ej._db_insert(type("J", (), {"id": "x", "user_id": str(uuid.uuid4()),
                                 "chat_id": None, "project_id": None,
                                 "worktree": None, "command": "echo"})())  # não levanta


# --------------------------------------------------------------------------- #
# Recuperação (Postgres real)
# --------------------------------------------------------------------------- #
def _ping() -> bool:
    try:
        c = ej._pg(); c.close(); return True
    except Exception:  # noqa: BLE001
        return False


def _sql(q, args=()):
    c = ej._pg()
    with c, c.cursor() as cur:
        cur.execute(q, args)
        rows = cur.fetchall() if cur.description else None
    c.close()
    return rows


def test_recover_orphans_destrava_e_poupa_o_processo_atual():
    if not _ping():
        pytest.skip("Postgres indisponível (rode dentro do container)")

    uid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    key_orphan = uuid.uuid4().hex[:12]
    key_mine = uuid.uuid4().hex[:12]
    _sql("INSERT INTO users (id,email,hashed_password,role,is_active,status,token_version,"
         "created_at,updated_at) VALUES (%s,%s,'x','user',true,'active',0,now(),now())",
         (uid, f"ej-{uid[:8]}@x.test"))
    _sql("INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES (%s,%s,'t',now(),now())",
         (cid, uid))

    def _mkjob(key, chat):
        _sql("INSERT INTO codespace_exec_jobs (id,job_key,user_id,chat_id,command,status,"
             "settled,output_tail,created_at,updated_at) VALUES (%s,%s,%s,%s,'mvn install',"
             "'running',false,'',now(),now())", (str(uuid.uuid4()), key, uid, chat))

    _mkjob(key_orphan, cid)   # órfão de um restart: deve ser recuperado + acordar o chat
    _mkjob(key_mine, None)    # "job deste processo": está no _jobs → deve ser poupado
    ej._jobs[key_mine] = object()  # simula um job vivo no processo atual

    woke: list[str] = []

    async def _fake_resume(chat_id, note, **kw):
        woke.append(str(chat_id))

    import aiworkspace.chat.resume as resume_mod
    orig = resume_mod.resume_chat_turn
    resume_mod.resume_chat_turn = _fake_resume
    try:
        async def _run():
            await ej.recover_orphans()
            # recover_orphans usa o SessionLocal/engine GLOBAL (correto: no boot real roda
            # no main loop). Aqui rodamos num asyncio.run próprio, cujo loop FECHA ao fim —
            # as conexões do pool ficariam atadas a um loop morto e poluiriam o próximo teste
            # que use SessionLocal (o de investigação pinga e falharia). Descartar o engine
            # no MESMO loop fecha as conexões limpo. Ver docstring do _run_db lá.
            from aiworkspace.db import engine
            await engine.dispose()
        asyncio.run(_run())

        # órfão → interrompido + settled, e o chat foi acordado
        row = _sql("SELECT status,settled FROM codespace_exec_jobs WHERE job_key=%s", (key_orphan,))[0]
        assert row[0] == "interrupted" and row[1] is True, row
        assert woke == [cid], woke
        # job do processo atual → intocado (ainda running/settled=False)
        mine = _sql("SELECT status,settled FROM codespace_exec_jobs WHERE job_key=%s", (key_mine,))[0]
        assert mine[0] == "running" and mine[1] is False, mine
        # gravou o evento de saúde da interrupção
        n_ev = _sql("SELECT count(*) FROM health_events WHERE capability='exec_jobs' "
                    "AND event='interrupted' AND user_id=%s", (uid,))[0][0]
        assert n_ev >= 1
    finally:
        resume_mod.resume_chat_turn = orig
        ej._jobs.pop(key_mine, None)
        _sql("DELETE FROM codespace_exec_jobs WHERE job_key IN (%s,%s)", (key_orphan, key_mine))
        _sql("DELETE FROM health_events WHERE user_id=%s", (uid,))
        _sql("DELETE FROM users WHERE id=%s", (uid,))  # CASCADE derruba o chat
