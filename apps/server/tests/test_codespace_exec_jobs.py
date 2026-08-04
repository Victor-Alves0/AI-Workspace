"""Execução em background do Codespace (exec_jobs) + detecção de comando longo.

Hermético: `start_job` sobe um subprocesso de shell REAL (echo/exit) num tempdir — sem rede,
sem DB. O wake é testado com stubs de `resume_chat_turn`/`generation.get_active`.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from aiworkspace.codespace import exec_jobs
from aiworkspace.tools.sift_service import _is_long_runner, _looks_like_server


# ------------------------------- detecção --------------------------------------

def test_long_runner_detects_installs_and_builds():
    for cmd in [
        "mvn -q test", "mise use -g java@21 maven", "npm install", "pnpm i",
        "pip install requests", "cargo build", "go mod download",
        "git clone https://x/y", "./gradlew build", "clojure -P",
        "wget https://x/y.jar", "apt-get install foo", "poetry add bar",
        # suítes de teste/lint/build também auto-backgroundam (podem estourar o teto
        # de CPU do run síncrono → SIGXCPU/exit 152) — ver _subcmd_is_long.
        "npm run lint", "pytest -k auth",
    ]:
        assert _is_long_runner(cmd), cmd


def test_long_runner_ignores_quick_commands():
    for cmd in [
        "ls -la", "cat pom.xml", "grep -r foo src",
        "python script.py", "echo hi", "git status",
    ]:
        assert not _is_long_runner(cmd), cmd


def test_looks_like_server_detecta_servidores():
    for cmd in [
        # o comando EXATO do bug do Metabase
        'MB_DB_TYPE=h2 HOST=0.0.0.0 PORT=4001 clojure -M:run:dev:dev-start',
        "npm run dev", "npm start", "yarn dev", "pnpm run serve",
        "next dev", "next start -p 3000", "vite", "ng serve", "astro dev",
        "python manage.py runserver 0.0.0.0:8000", "flask run --host 0.0.0.0",
        "uvicorn app:app --host 0.0.0.0", "gunicorn wsgi:app",
        "./gradlew bootRun", "rails server", "php -S 0.0.0.0:8000", "dotnet run",
    ]:
        assert _looks_like_server(cmd), cmd


def test_looks_like_server_ignora_builds_e_scripts():
    # instalar/buildar/testar COMPLETAM → não são servidores (vão pro bg job, não preview)
    for cmd in [
        "npm install", "npm run build", "npm run test", "mvn -q test", "./gradlew build",
        "pip install requests", "cargo build", "pytest -k auth",
        "clojure -M:build", "python script.py", "cat pom.xml", "ls -la",
    ]:
        assert not _looks_like_server(cmd), cmd


# ------------------------------- start/wait ------------------------------------

async def test_start_and_wait_job_success():
    with tempfile.TemporaryDirectory() as d:
        card = exec_jobs.start_job(Path(d), "echo hello-bg", chat_id="c1",
                                   user_id="u1", project_id="p1", worktree=None)
        assert card["kind"] == "job_started" and card["job_id"]
        res = await exec_jobs.wait_job(card["job_id"], timeout=30)
        assert res["status"] == "done" and res["exit_code"] == 0
        assert "hello-bg" in res["output"]
        # status idempotente + aparece no list do projeto
        assert exec_jobs.job_status(card["job_id"])["exit_code"] == 0
        assert any(j["job_id"] == card["job_id"] for j in exec_jobs.list_jobs(project_id="p1"))


async def test_wait_job_failure_exit_code():
    with tempfile.TemporaryDirectory() as d:
        card = exec_jobs.start_job(Path(d), "exit 3", chat_id="c2",
                                   user_id="u1", project_id="p1", worktree=None)
        res = await exec_jobs.wait_job(card["job_id"], timeout=30)
        assert res["status"] == "failed" and res["exit_code"] == 3


async def test_wait_job_unknown_id():
    res = await exec_jobs.wait_job("deadbeef00")
    assert res.get("error")


async def test_job_escopado_ao_projeto():
    """ISOLAMENTO: status/wait por job_id são escopados ao projeto. Um id REAL de outro
    projeto é tratado como inexistente (não vaza a saída). Sem project_id, resolve global."""
    with tempfile.TemporaryDirectory() as d:
        card = exec_jobs.start_job(Path(d), "echo x", chat_id="c", user_id="u1",
                                   project_id="pA", worktree=None)
        jid = card["job_id"]
        try:
            assert exec_jobs._jobs[jid]._evt.wait(10)
            # projeto certo → enxerga; projeto errado → not-found (sem vazar)
            assert exec_jobs.job_status(jid, project_id="pA").get("job_id") == jid
            assert exec_jobs.job_status(jid, project_id="pB").get("error")
            w = await exec_jobs.wait_job(jid, timeout=5, project_id="pB")
            assert w.get("error") and "x" not in str(w.get("output", ""))
            # sem escopo (compat) → resolve
            assert exec_jobs.job_status(jid).get("job_id") == jid
        finally:
            exec_jobs._jobs.pop(jid, None)


async def test_start_job_empty_command():
    with tempfile.TemporaryDirectory() as d:
        assert exec_jobs.start_job(Path(d), "  ", chat_id="c", user_id="u",
                                   project_id="p", worktree=None).get("error")


# --------------------------------- wake ----------------------------------------

async def test_maybe_wake_fires_when_idle():
    """Job concluído + ninguém esperou + chat ocioso → dispara resume_chat_turn."""
    import aiworkspace.chat.resume as resume_mod
    from aiworkspace.chat import generation as gen
    calls: list = []

    async def fake_resume(chat_id, text, *, notify_title, notify_body=""):
        calls.append((chat_id, text, notify_title))

    orig_resume, orig_active = resume_mod.resume_chat_turn, gen.get_active
    resume_mod.resume_chat_turn = fake_resume
    gen.get_active = lambda cid: None
    try:
        with tempfile.TemporaryDirectory() as d:
            card = exec_jobs.start_job(Path(d), "echo x", chat_id="cw",
                                       user_id="u1", project_id="p1", worktree=None)
            job = exec_jobs._jobs[card["job_id"]]
            assert job._evt.wait(10)          # espera a thread leitora concluir
            await exec_jobs._maybe_wake(job)
            assert calls and calls[0][0] == "cw" and job.dispatched
            assert "echo x" in calls[0][1]    # a nota carrega o comando
    finally:
        resume_mod.resume_chat_turn, gen.get_active = orig_resume, orig_active


async def test_reaper_nao_duplica_wake_com_chat_ocupado():
    """REGRESSÃO (dinheiro): o job termina enquanto o chat AINDA gera (o agente soltou o
    job e segue escrevendo o fim do turno). O reaper varre a cada 2s; a decisão de wake
    (`dispatched`) só é tomada no FIM de `_maybe_wake`, após esperar o chat ficar ocioso
    (até ~120s). Sem a reivindicação síncrona (`_claimed`), o reaper criava uma task nova
    a cada ciclo → N `_fire_wake` = N gerações de modelo duplicadas quando o chat enfim
    ficasse ocioso. Tem que disparar UM ÚNICO wake."""
    import asyncio

    import aiworkspace.chat.resume as resume_mod
    from aiworkspace.chat import generation as gen
    calls: list = []

    async def fake_resume(chat_id, text, *, notify_title, notify_body=""):
        calls.append(chat_id)

    class _Gen:
        done = False

    state = {"polls": 0}

    def fake_active(cid):            # "ocupado" nos primeiros ~4 polls (≈4s), depois ocioso
        state["polls"] += 1
        return _Gen() if state["polls"] <= 4 else None

    orig_resume, orig_active = resume_mod.resume_chat_turn, gen.get_active
    resume_mod.resume_chat_turn = fake_resume
    gen.get_active = fake_active
    exec_jobs._reaper_task = None
    try:
        with tempfile.TemporaryDirectory() as d:
            card = exec_jobs.start_job(Path(d), "echo x", chat_id="cbusy",
                                       user_id="u1", project_id="p1", worktree=None)
            job = exec_jobs._jobs[card["job_id"]]
            assert job._evt.wait(10)
            exec_jobs.start_reaper()   # varre a cada 2s enquanto o chat está "ocupado"
            await asyncio.sleep(7)     # cobre ~3 ciclos do reaper (t≈0,2,4,6)
    finally:
        if exec_jobs._reaper_task:
            exec_jobs._reaper_task.cancel()
        resume_mod.resume_chat_turn, gen.get_active = orig_resume, orig_active
        exec_jobs._jobs.pop(card["job_id"], None)
    assert len(calls) == 1, f"esperava 1 wake, veio {len(calls)}"
    assert calls[0] == "cbusy" and job.dispatched


async def test_maybe_wake_skips_when_waited():
    """Se um wait_job já consumiu o resultado, o wake NÃO dispara (sem turno duplicado)."""
    import aiworkspace.chat.resume as resume_mod
    calls: list = []

    async def fake_resume(*a, **k):
        calls.append(1)

    orig = resume_mod.resume_chat_turn
    resume_mod.resume_chat_turn = fake_resume
    try:
        with tempfile.TemporaryDirectory() as d:
            card = exec_jobs.start_job(Path(d), "echo x", chat_id="cw2",
                                       user_id="u1", project_id="p1", worktree=None)
            job = exec_jobs._jobs[card["job_id"]]
            assert job._evt.wait(10)
            job.waited = True
            await exec_jobs._maybe_wake(job)
            assert not calls and job.dispatched
    finally:
        resume_mod.resume_chat_turn = orig
