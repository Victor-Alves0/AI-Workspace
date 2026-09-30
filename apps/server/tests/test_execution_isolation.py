"""Onde roda o código da IA (execution.py) e o executor isolado (runner/service.py).

No perfil servidor, comandos/previews/run_code/ferramentas criadas rodam no container
`runner` (sem segredos, sem banco). Produção SEM executor não roda nada: rodar dentro
do server exporia o APP_SECRET/banco a uma injeção de prompt. Desktop/dev rodam na
própria máquina (é a funcionalidade)."""
from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiworkspace import execution


def _settings(monkeypatch, **kw):
    base = dict(code_exec_isolation="auto", code_runner_url="", code_runner_token="",
                code_runner_token_file="", is_production=False)
    base.update(kw)
    monkeypatch.setattr(execution, "get_settings", lambda: SimpleNamespace(**base))


def test_mode_decision(monkeypatch):
    _settings(monkeypatch)
    assert execution.mode() == "host"                          # desktop/dev
    _settings(monkeypatch, is_production=True)
    assert execution.mode() == "off"                           # servidor sem executor
    _settings(monkeypatch, is_production=True, code_runner_url="ws://runner:8765")
    assert execution.mode() == "runner"
    assert execution.target_host() == "runner"
    _settings(monkeypatch, code_exec_isolation="runner")       # forçado sem URL
    assert execution.mode() == "off"
    _settings(monkeypatch, code_exec_isolation="host", is_production=True)
    assert execution.mode() == "host"


def test_off_refuses_commands_and_code_mode(monkeypatch, tmp_path):
    from aiworkspace.chat import turn_setup
    from aiworkspace.codespace import exec_service

    _settings(monkeypatch, is_production=True)
    out = exec_service.run_command(tmp_path, "echo oi")
    assert out == {"error": execution.OFF_MESSAGE}
    with pytest.raises(OSError):
        exec_service.spawn_host("echo oi", tmp_path)
    mc = SimpleNamespace(tools_enabled=True, code_mode=True)
    monkeypatch.setattr(turn_setup, "get_settings", lambda: SimpleNamespace(allow_code_mode=True))
    assert turn_setup._code_mode(mc) is False


def test_server_folders_stay_in_the_project_area(monkeypatch, tmp_path):
    from aiworkspace.codespace import graph_service as gs

    monkeypatch.setattr(gs, "data_root", lambda: tmp_path / "cs")
    (tmp_path / "cs" / "u").mkdir(parents=True)
    _settings(monkeypatch, is_production=True, code_runner_url="ws://runner:8765")
    assert gs.folders_anywhere() is False
    assert gs.within_data_root(tmp_path / "cs" / "u") is True
    assert gs.within_data_root(tmp_path / "fora") is False
    assert gs.within_data_root(tmp_path / "cs" / ".." / "fora") is False
    _settings(monkeypatch)
    assert gs.folders_anywhere() is True                       # desktop: o disco é do usuário


def test_runner_env_never_carries_secrets(monkeypatch):
    from aiworkspace.runner import service

    monkeypatch.setenv("APP_SECRET", "x" * 40)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("RUNNER_TOKEN_FILE", "/run/runner/token")
    env = service._env({"PORT": "4001"})
    assert "APP_SECRET" not in env and "OPENROUTER_API_KEY" not in env
    assert not any(k.startswith("RUNNER_") for k in env)
    assert env["PORT"] == "4001" and env["AIWORKSPACE_SANDBOX"] == "1"


def test_runner_token_is_created_once(tmp_path):
    from aiworkspace.runner import service

    p = tmp_path / "state" / "token"
    a = service.load_token(str(p))
    assert len(a) > 30 and service.load_token(str(p)) == a
    if os.name != "nt":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_runner_cwd_must_be_inside_roots(monkeypatch, tmp_path):
    from aiworkspace.runner import service

    monkeypatch.setenv("RUNNER_ROOTS", str(tmp_path / "cs"))
    (tmp_path / "cs" / "p").mkdir(parents=True)
    assert service._inside(tmp_path / "cs" / "p")
    assert not service._inside(tmp_path)
    assert not service._inside(tmp_path / "cs" / ".." / "x")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(os.name == "nt", reason="o executor é Linux (setsid/killpg)")
def test_remote_proc_end_to_end(monkeypatch, tmp_path):
    """Executor de verdade numa thread + RemoteProc: saída, stdin, erro separado,
    timeout que mata a árvore, e recusa sem token."""
    from aiworkspace.runner import service

    monkeypatch.setenv("RUNNER_ROOTS", str(tmp_path))
    port = _free_port()
    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: loop.run_until_complete(service.serve("127.0.0.1", port, "tok")),
                     daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    _settings(monkeypatch, code_runner_url=f"ws://127.0.0.1:{port}", code_runner_token="tok")

    p = execution.RemoteProc(shell="echo oi; echo erro >&2; exit 3", cwd=tmp_path)
    out, _ = p.communicate(timeout=10)
    assert p.returncode == 3 and "oi" in out and "erro" in out

    p = execution.RemoteProc(argv=["python3", "-c", "import sys;print(sys.stdin.read().upper())"],
                             cwd=tmp_path, stdin=True, merge_stderr=False)
    out, err = p.communicate("abc", timeout=10)
    assert out.strip() == "ABC" and err == ""

    p = execution.RemoteProc(shell="sleep 30 & sleep 30", cwd=tmp_path)
    with pytest.raises(Exception):
        p.wait(timeout=0.5)
    p.kill()
    assert p.wait(timeout=10) is not None

    with pytest.raises(OSError):
        execution.RemoteProc(shell="echo x", cwd="/")        # fora das raízes

    _settings(monkeypatch, code_runner_url=f"ws://127.0.0.1:{port}", code_runner_token="errado")
    with pytest.raises(OSError):
        execution.RemoteProc(shell="echo x", cwd=tmp_path)
