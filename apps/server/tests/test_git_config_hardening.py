"""O git do SERVIDOR não executa programas plantados na pasta do projeto.

Os comandos da IA rodam no executor isolado, mas gravam na mesma pasta que o servidor
usa para commit/status/diff/push. Um `.git/config` com fsmonitor/filtro/pager, ou um
hook, faria o git do servidor rodar código ao lado dos segredos — furando o executor.
E um `origin` trocado levaria o token do push para outro lugar."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aiworkspace.codespace import graph_service as gs

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "a.txt").write_text("oi\n", encoding="utf-8")
    return root


def _plant(root: Path, marker: Path) -> None:
    script = root.parent / "evil.py"
    script.write_text(f"open(r'{marker}', 'a').write('x')\n", encoding="utf-8")
    cmd = f"python {script.as_posix()}"
    cfg = str(root / ".git" / "config")
    for k, v in (("core.fsmonitor", cmd), ("core.pager", cmd), ("filter.evil.clean", cmd),
                 ("filter.evil.smudge", cmd), ("diff.evil.textconv", cmd),
                 ("include.path", "/tmp/nao-existe")):
        subprocess.run(["git", "config", "--file", cfg, k, v], check=True)
    (root / ".gitattributes").write_text("*.txt filter=evil diff=evil\n", encoding="utf-8")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{cmd}\n", encoding="utf-8")
    hook.chmod(0o755)


def test_server_git_ignores_planted_programs(tmp_path):
    root = _repo(tmp_path)
    marker = tmp_path / "pwned"
    _plant(root, marker)
    out = gs._git_commit(root, "teste")
    assert out and out.get("sha")
    gs._git(root, "status")
    gs._git(root, "diff", "HEAD~0")
    assert not marker.exists(), "o git do servidor executou um programa plantado no repo"
    cfg = (root / ".git" / "config").read_text(encoding="utf-8")
    assert "fsmonitor" not in cfg and "evil" not in cfg and "include" not in cfg.lower()
    assert "repositoryformatversion" in cfg  # o resto da config fica


def test_scrub_is_skipped_when_config_did_not_change(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    gs._git(root, "status")
    calls = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        calls.append(cmd)
        return real(cmd, *a, **k)

    monkeypatch.setattr(gs.subprocess, "run", spy)
    gs._git(root, "status")
    assert not any("--list" in c for c in calls)  # nada mudou: não relê


def test_push_goes_to_the_registered_url_not_the_planted_origin(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "https://atacante.example/x.git"], check=True)
    seen = {}

    def fake_run(cmd, *a, **k):
        if "push" in cmd:
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run.__wrapped__(cmd, *a, **k) if hasattr(subprocess.run, "__wrapped__") else real(cmd, *a, **k)

    real = subprocess.run
    monkeypatch.setattr(gs.subprocess, "run", fake_run)
    gs._push(root, "main", "TOKEN", None, "https://github.com/dono/repo.git")
    assert "https://github.com/dono/repo.git" in seen["cmd"]
    assert "origin" not in seen["cmd"]
    assert "core.hooksPath=/dev/null" in seen["cmd"]
