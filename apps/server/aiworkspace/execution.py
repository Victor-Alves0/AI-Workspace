"""Onde roda o código decidido pela IA: no executor isolado, na própria máquina, ou em
lugar nenhum.

- "runner": container `runner` (perfil servidor/Docker). Comandos do Codespace, os
  previews, o run_code e as ferramentas criadas rodam lá, longe dos segredos do server.
- "host": subprocesso na própria máquina. É o app desktop (a máquina É do usuário) e o
  desenvolvimento local.
- "off": produção sem executor configurado. Rodar ali seria rodar dentro do server,
  ao lado do APP_SECRET e do banco — então a execução fica desligada, com um erro que
  diz como ligar.

`CODE_EXEC_ISOLATION` força um modo; o padrão ("auto") decide pelo acima.

`RemoteProc` imita o pedaço do `subprocess.Popen` que o app usa (stdout/stderr/stdin,
poll/wait/communicate/kill/returncode/pid), falando com o executor por WebSocket — por
isso exec síncrono, jobs em background, previews e run_code trocam de lugar sem mudar
de lógica.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import get_settings

OFF_MESSAGE = ("execução de código desligada neste servidor: não há executor isolado "
               "configurado (serviço `runner` do docker-compose / CODE_RUNNER_URL). Rodar "
               "dentro do próprio servidor exporia os segredos dele.")


def mode() -> str:
    s = get_settings()
    forced = (getattr(s, "code_exec_isolation", "auto") or "auto").strip().lower()
    if forced in ("runner", "host", "off"):
        if forced == "runner" and not s.code_runner_url:
            return "off"
        return forced
    if s.code_runner_url:
        return "runner"
    return "off" if s.is_production else "host"


def target_host() -> str:
    """Onde os previews escutam: no executor (rede interna) ou na própria máquina."""
    if mode() == "runner":
        return urlparse(get_settings().code_runner_url).hostname or "127.0.0.1"
    return "127.0.0.1"


def _token() -> str:
    s = get_settings()
    path = (getattr(s, "code_runner_token_file", "") or "").strip()
    if path:
        try:
            tok = Path(path).read_text(encoding="utf-8").strip()
            if tok:
                return tok
        except OSError:
            pass
    return (s.code_runner_token or "").strip()


def _ws_url() -> str:
    u = get_settings().code_runner_url.rstrip("/")
    if u.startswith("http://"):
        u = "ws://" + u[7:]
    elif u.startswith("https://"):
        u = "wss://" + u[8:]
    return u + "/proc"


class _Pipe:
    """Texto que chega aos pedaços e é lido como arquivo (readline/read/iteração)."""

    def __init__(self) -> None:
        self._buf = ""
        self._eof = False
        self._cv = threading.Condition()

    def _feed(self, text: str) -> None:
        with self._cv:
            self._buf += text
            self._cv.notify_all()

    def _close(self) -> None:
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def readline(self) -> str:
        with self._cv:
            while True:
                i = self._buf.find("\n")
                if i >= 0:
                    line, self._buf = self._buf[:i + 1], self._buf[i + 1:]
                    return line
                if self._eof:
                    line, self._buf = self._buf, ""
                    return line
                self._cv.wait()

    def read(self) -> str:
        with self._cv:
            while not self._eof:
                self._cv.wait()
            out, self._buf = self._buf, ""
            return out

    def __iter__(self):
        while True:
            line = self.readline()
            if not line:
                return
            yield line

    def close(self) -> None:
        pass


class _Stdin:
    def __init__(self, proc: "RemoteProc") -> None:
        self._p = proc
        self.closed = False

    def write(self, text: str) -> int:
        self._p._send({"t": "in", "d": text})
        return len(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._p._send({"t": "eof"})


class RemoteProc:
    """Processo rodando no executor isolado, com a cara de um `subprocess.Popen`."""

    def __init__(self, *, shell: str | None = None, argv: list[str] | None = None,
                 cwd: str | Path, env: dict[str, str] | None = None,
                 cpu_seconds: int = 0, mem_mb: int = 0, stdin: bool = False,
                 merge_stderr: bool = True, connect_timeout: float = 10.0) -> None:
        from websockets.sync.client import connect

        self.args = shell if shell is not None else argv
        self.returncode: int | None = None
        self.pid: int = -1
        self.stdout = _Pipe()
        self.stderr = None if merge_stderr else _Pipe()
        self.stdin = _Stdin(self) if stdin else None
        self._done = threading.Event()
        self._send_lock = threading.Lock()
        tok = _token()
        try:
            self._ws = connect(_ws_url(), open_timeout=connect_timeout, max_size=32 * 1024 * 1024,
                               additional_headers={"Authorization": f"Bearer {tok}"} if tok else None)
        except Exception as exc:  # noqa: BLE001
            raise OSError(f"o executor isolado não respondeu ({str(exc)[:160]})") from exc
        spec: dict[str, Any] = {"cwd": str(cwd), "env": env or {}, "cpu_seconds": int(cpu_seconds),
                                "mem_mb": int(mem_mb), "stdin": bool(stdin), "merge_stderr": bool(merge_stderr)}
        if shell is not None:
            spec["shell"] = shell
        else:
            spec["argv"] = list(argv or [])
        self._ws.send(json.dumps(spec))
        # a primeira resposta diz se o processo nasceu (pid) ou por que não
        first = json.loads(self._ws.recv(timeout=connect_timeout))
        if first.get("t") != "started":
            self._close_ws()
            raise OSError(str(first.get("d") or "o executor recusou o comando"))
        self.pid = int(first.get("pid") or -1)
        threading.Thread(target=self._reader, daemon=True, name=f"remote-proc-{self.pid}").start()

    # -- transporte ----------------------------------------------------------
    def _send(self, msg: dict) -> None:
        if self._done.is_set():
            return
        try:
            with self._send_lock:
                self._ws.send(json.dumps(msg))
        except Exception:  # noqa: BLE001 - conexão caiu: o leitor encerra
            pass

    def _reader(self) -> None:
        try:
            for raw in self._ws:
                msg = json.loads(raw)
                t = msg.get("t")
                if t == "out":
                    self.stdout._feed(msg.get("d") or "")
                elif t == "err":
                    (self.stderr or self.stdout)._feed(msg.get("d") or "")
                elif t == "exit":
                    self.returncode = int(msg.get("code") if msg.get("code") is not None else -9)
                    break
        except Exception:  # noqa: BLE001
            pass
        finally:
            if self.returncode is None:
                self.returncode = -9  # conexão caiu sem "exit": tratamos como morto
            self.stdout._close()
            if self.stderr is not None:
                self.stderr._close()
            self._done.set()
            self._close_ws()

    def _close_ws(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    # -- API do Popen --------------------------------------------------------
    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode  # type: ignore[return-value]

    def communicate(self, input: str | None = None, timeout: float | None = None):
        if input is not None and self.stdin is not None:
            self.stdin.write(input)
        if self.stdin is not None:
            self.stdin.close()
        self.wait(timeout)
        out = self.stdout.read()
        err = self.stderr.read() if self.stderr is not None else None
        return out, err

    def kill(self) -> None:
        self._send({"t": "kill"})

    terminate = kill

    def send_signal(self, _sig: int) -> None:
        self.kill()
