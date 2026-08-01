"""Sandbox de execução do Codespace: roda comandos do projeto (testes/build/lint).

Baseline UNIVERSAL = subprocesso no host — funciona em Windows, Linux e no desktop
sem Docker. `run_command` executa o comando com o CWD dentro do projeto/worktree,
com env higienizado (sem segredos do servidor), timeout rígido com kill da ÁRVORE de
processos (build spawna filhos) e teto de saída. No Linux ganha RLIMIT_CPU.

Se `settings.code_runner_url` estiver setado, encaminha para um container `runner`
(isolamento mais forte) via HTTP; senão, roda no host. O chamador é sempre síncrono
(rodado em threadpool pela SIFT), como as demais tools do Codespace.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_IS_WINDOWS = os.name == "nt"

# Chaves de ambiente que NUNCA vão para o subprocesso (segredos do servidor). Não é
# allowlist — preservamos PATH/HOME/locale (o build precisa) e removemos o sensível.
_SECRET_HINTS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY",
                 "APIKEY", "PRIVATE", "DATABASE_URL", "DSN", "APP_SECRET",
                 "OPENROUTER", "ANTHROPIC", "OPENAI", "AWS_", "SMTP")


def _clean_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not any(h in k.upper() for h in _SECRET_HINTS)}
    # marcador p/ o código do projeto saber que roda no sandbox (CI-like)
    env["AIWORKSPACE_SANDBOX"] = "1"
    env["CI"] = env.get("CI", "1")
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _rlimit_preexec(cpu_seconds: int):
    """POSIX: limita CPU do processo (o timeout de wall-clock cobre o resto). Nada de
    RLIMIT_AS — compiladores/bundlers precisam de muita memória virtual."""
    if _IS_WINDOWS:
        return None

    def _apply() -> None:  # pragma: no cover - roda no filho
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        except Exception:
            pass
        try:
            os.setsid()  # grupo próprio → dá p/ matar a árvore inteira no timeout
        except Exception:
            pass

    return _apply


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if _IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass


def spawn_host(command: str, root: Path, env_extra: dict | None = None) -> subprocess.Popen:
    """Sobe o subprocesso no host (shell, CWD=root, env higienizado, grupo próprio p/
    matar a árvore). Compartilhado pelo run SÍNCRONO (`_run_host`) e pelo background
    (`exec_jobs`). Levanta OSError/ValueError se não conseguir iniciar."""
    s = get_settings()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
    return subprocess.Popen(
        command, shell=True, cwd=str(root),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", env=_clean_env(env_extra),
        preexec_fn=_rlimit_preexec(int(s.code_exec_cpu_seconds)),
        creationflags=creationflags,
    )


def _setsid_preexec():
    """POSIX: só o grupo próprio (p/ matar a árvore) — SEM RLIMIT_CPU."""
    if _IS_WINDOWS:
        return None

    def _apply() -> None:  # pragma: no cover - roda no filho
        try:
            os.setsid()
        except Exception:
            pass

    return _apply


def spawn_server(command: str, root: Path, env_extra: dict | None = None) -> subprocess.Popen:
    """Como `spawn_host`, mas SEM RLIMIT_CPU: um servidor de preview (dev server/
    backend) roda por horas e o limite de CPU o mataria. Mantém o env higienizado e o
    grupo de processos próprio (p/ `kill_tree` derrubar a árvore). Só o preview_service
    usa isto — o run normal continua com o limite de CPU."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0
    return subprocess.Popen(
        command, shell=True, cwd=str(root),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", env=_clean_env(env_extra),
        preexec_fn=_setsid_preexec(),
        creationflags=creationflags,
    )


def cap_output(out: str | None) -> tuple[str, bool]:
    """Corta a saída ao teto de bytes (mantém a CAUDA — o fim do build/log é o que
    importa). Devolve (texto, truncado?)."""
    out = out or ""
    cap = int(get_settings().code_exec_output_bytes)
    truncated = len(out.encode("utf-8", "ignore")) > cap
    if truncated:
        out = out.encode("utf-8", "ignore")[-cap:].decode("utf-8", "ignore")
    return out, truncated


# reexporta o kill da árvore p/ o exec_jobs (mesmo grupo de processos)
kill_tree = _kill_tree


def _run_host(command: str, root: Path, timeout: int, env_extra: dict | None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = spawn_host(command, root, env_extra)
    except (OSError, ValueError) as exc:
        return {"error": f"não consegui iniciar o comando: {exc}"}

    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            out = ""
    out, truncated = cap_output(out)
    return {
        "exit_code": None if timed_out else proc.returncode,
        "output": out,
        "seconds": round(time.monotonic() - started, 2),
        "truncated": truncated,
        "timed_out": timed_out,
    }


def _run_via_runner(command: str, rel_cwd: str, timeout: int, env_extra: dict | None) -> dict[str, Any]:
    """Encaminha p/ o container `runner` (opt-in). Contrato mínimo:
    POST {url}/run {command, cwd (relativo ao codespace_data montado), timeout, env}
    -> {exit_code, output, seconds, truncated, timed_out}. Bearer = code_runner_token."""
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.code_runner_token}"} if s.code_runner_token else {}
    try:
        r = httpx.post(
            s.code_runner_url.rstrip("/") + "/run",
            json={"command": command, "cwd": rel_cwd, "timeout": timeout, "env": env_extra or {}},
            headers=headers, timeout=timeout + 30,
        )
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        logger.warning("runner exec falhou: %s", exc)
        return {"error": f"o serviço runner não respondeu ({str(exc)[:200]}) — verifique CODE_RUNNER_URL "
                         "ou desative-o para rodar no host"}


def run_command(root: Path, command: str, *, data_root: Path | None = None,
                timeout: int | None = None, env_extra: dict | None = None) -> dict[str, Any]:
    """Roda `command` com CWD=`root`. `root` deve estar dentro do codespace_data
    (jail garantido pelo chamador). Devolve {exit_code, output, seconds, ...} ou {error}."""
    command = (command or "").strip()
    if not command:
        return {"error": "comando vazio"}
    root = Path(root)
    if not root.is_dir():
        return {"error": "diretório de trabalho do projeto não existe"}
    s = get_settings()
    timeout = int(timeout or s.code_exec_timeout_seconds)
    if s.code_runner_url:
        # o runner monta o mesmo volume; passa o caminho RELATIVO ao data_root
        try:
            rel = str(root.relative_to(data_root)) if data_root else str(root)
        except ValueError:
            rel = str(root)
        return _run_via_runner(command, rel, timeout, env_extra)
    return _run_host(command, root, timeout, env_extra)
