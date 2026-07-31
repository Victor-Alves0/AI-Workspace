"""Execução em background do Codespace (exec_jobs) + detecção de comando longo.

Hermético: `start_job` sobe um subprocesso de shell REAL (echo/exit) num tempdir — sem rede,
sem DB. O wake é testado com stubs de `resume_chat_turn`/`generation.get_active`.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from aiworkspace.codespace import exec_jobs
from aiworkspace.tools.sift_service import _is_long_runner


# ------------------------------- detecção --------------------------------------

def test_long_runner_detects_installs_and_builds():
    for cmd in [
        "mvn -q test", "mise use -g java@21 maven", "npm install", "pnpm i",
        "pip install requests", "cargo build", "go mod download",
        "git clone https://x/y", "./gradlew build", "clojure -P",
        "wget https://x/y.jar", "apt-get install foo", "poetry add bar",
    ]:
        assert _is_long_runner(cmd), cmd


def test_long_runner_ignores_quick_commands():
    for cmd in [
        "ls -la", "cat pom.xml", "grep -r foo src", "npm run lint",
        "python script.py", "echo hi", "git status", "pytest -k auth",
    ]:
        assert not _is_long_runner(cmd), cmd


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
