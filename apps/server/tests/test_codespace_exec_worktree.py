"""Codespace: sandbox de execução (exec_service) + mecânica de worktree.

Puro/hermético — usa git real em tmp_path (como test_codespace_graph_service), sem
DB nem rede. Cobre o que, se sair errado, quebra o loop de programação: exec com
env higienizado/timeout/jail, o parsing de owner/repo do PR, o numstat do diff e a
escrita ciente-de-worktree + merge com identidade git.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from aiworkspace.codespace import exec_service
from aiworkspace.codespace import graph_service as gs
from aiworkspace.codespace import worktree_service as ws


# --------------------------------------------------------------------------- #
# exec_service (subprocesso no host)
# --------------------------------------------------------------------------- #
def test_exec_runs_and_reports_exit_code(tmp_path):
    r = exec_service.run_command(tmp_path, "echo hi && exit 0")
    assert r["exit_code"] == 0
    assert "hi" in r["output"]
    assert r["timed_out"] is False


def test_exec_nonzero_exit(tmp_path):
    r = exec_service.run_command(tmp_path, "exit 7")
    assert r["exit_code"] == 7


def test_exec_empty_command_errors(tmp_path):
    assert "error" in exec_service.run_command(tmp_path, "   ")


def test_exec_missing_dir_errors(tmp_path):
    assert "error" in exec_service.run_command(tmp_path / "nope", "echo x")


def test_exec_timeout_kills(tmp_path):
    r = exec_service.run_command(tmp_path, "sleep 5", timeout=1)
    assert r["timed_out"] is True
    assert r["exit_code"] is None


@pytest.mark.skipif(os.name == "nt", reason="printenv é POSIX")
def test_exec_scrubs_secrets_keeps_path(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET", "supersecret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-xxx")
    monkeypatch.setenv("HARMLESS_VAR", "keepme")
    r = exec_service.run_command(tmp_path, "printenv")
    out = r["output"]
    assert "supersecret" not in out
    assert "sk-xxx" not in out
    assert "keepme" in out          # var inócua preservada
    assert "AIWORKSPACE_SANDBOX" in out  # marcador do sandbox injetado


# --------------------------------------------------------------------------- #
# worktree_service — helpers puros
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,expected", [
    ("https://github.com/octo/hello.git", "octo/hello"),
    ("https://github.com/octo/hello", "octo/hello"),
    ("git@github.com:octo/hello.git", "octo/hello"),
    ("https://gitlab.com/group/sub/repo.git", "group/sub"),
    ("", ""),
    ("not-a-url", ""),
])
def test_repo_slug(url, expected):
    assert ws._repo_slug(url) == expected


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)], capture_output=True, check=True)
    (path / "a.txt").write_text("one\n")
    gs._git_commit(path, "init")


def test_numstat_counts_changes(tmp_path):
    # como no fluxo real, as mudanças da tarefa já estão COMMITADAS (write_file
    # commita); numstat compara a base (commit anterior) com o HEAD.
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "b.txt").write_text("new\n")
    gs._git_commit(tmp_path, "work")
    stat = ws._numstat(tmp_path, "HEAD~1")
    assert stat["files"] == 2
    assert stat["insertions"] >= 2
    assert stat["deletions"] == 0


# --------------------------------------------------------------------------- #
# Escrita ciente-de-worktree + merge (a mecânica que o worktree_service usa)
# --------------------------------------------------------------------------- #
def test_worktree_write_and_merge(tmp_path, monkeypatch):
    # jail: aponta o data root para tmp_path (write_file valida caminho contra o root)
    monkeypatch.setattr(gs, "_DATA_ROOT", tmp_path)
    src = tmp_path / "u" / "p" / "src"
    src.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(src)], capture_output=True, check=True)
    (src / "a.txt").write_text("one\n")
    gs._git_commit(src, "init")

    # abre worktree pela mesma chamada git do serviço
    wt = gs.wt_dir("u", "p", "task1")
    wt.parent.mkdir(parents=True, exist_ok=True)
    proc = gs._git(src, "worktree", "add", "-b", "codespace/task1", str(wt), "main")
    assert proc.returncode == 0

    # escrita ciente-de-worktree: commita na branch do worktree, sem reindex
    out = gs.write_file("u", "p", {}, "b.txt", "hello\n", root=wt, reindex=False)
    assert out["ok"] and out["commit"]
    assert (wt / "b.txt").exists()
    assert not (src / "b.txt").exists()  # NÃO vaza para o src antes do merge

    # merge com identidade git (o bug real que os testes pegaram: --no-ff sem -c → rc 128)
    name, email = gs._GIT_AUTHOR
    m = gs._git(src, "-c", f"user.name={name}", "-c", f"user.email={email}",
                "merge", "--no-ff", "codespace/task1", "-m", "merge")
    assert m.returncode == 0
    assert (src / "b.txt").read_text() == "hello\n"
