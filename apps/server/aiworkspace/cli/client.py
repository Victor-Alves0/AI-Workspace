"""Conexão do CLI com o motor local: acha (ou sobe) o motor, entra pelo token local
(POST /auth/local) e fala com a mesma API que a interface usa.

Só stdlib + httpx (que o backend já traz): o CLI chega pela atualização leve do app,
então não pode depender de nada que o motor instalado não tenha.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

APP_ID = "com.aiworkspace.app"  # identifier do tauri.conf.json: a pasta de dados do app
LOCAL_TOKEN_HEADER = "x-aiw-local-token"


class CliError(Exception):
    """Erro para o usuário: vira uma linha na tela, sem traceback."""


def data_dir() -> Path:
    """A MESMA pasta de dados do app desktop: CLI e janela veem os mesmos chats."""
    if v := os.environ.get("AIW_DATA_DIR"):
        return Path(v)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_ID / "data"


def engine_root() -> Path | None:
    """Pasta do motor (definida pelo bootstrap aiw.py). None = rodando fora do motor."""
    v = os.environ.get("AIW_ENGINE_ROOT")
    return Path(v) if v else None


def _health(port: int) -> bool:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def live_port(data: Path) -> int | None:
    """Porta do motor que está no ar com ESTES dados (o launcher grava port.txt)."""
    try:
        port = int((data / "port.txt").read_text().strip())
    except (OSError, ValueError):
        return None
    return port if _health(port) else None


class Engine:
    """O motor em uso: o que já estava no ar, ou um que este CLI subiu.

    Um motor subido pelo CLI vive enquanto o CLI viver: no Linux o launcher pede ao
    kernel um SIGTERM quando o processo pai sai (PR_SET_PDEATHSIG); no Windows o
    `close()` derruba a árvore. Nada fica rodando escondido depois do `aiw`.
    """

    def __init__(self, data: Path) -> None:
        self.data = data
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None

    def ensure(self, status=None) -> int:
        self.port = live_port(self.data)
        if self.port:
            return self.port
        root = engine_root()
        if root is None:
            raise CliError("o motor do AI Workspace nao esta no ar (abra o app ou rode `aiw` instalado)")
        self.data.mkdir(parents=True, exist_ok=True)
        log = open(self.data / "cli-engine.log", "ab")  # noqa: SIM115 - fica com o processo
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH")}
        if sys.platform == "win32":
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", "Start-AIWorkspace.ps1", "-DataDir", str(self.data), "-NoBrowser"]
            flags = 0x0800_0000  # CREATE_NO_WINDOW
        else:
            cmd = [sys.executable, str(root / "launcher.py"), "--data-dir", str(self.data), "--no-browser"]
            flags = 0
        self.proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 900:  # 1o boot baixa o modelo de embeddings
            if self.proc.poll() is not None:
                raise CliError(f"o motor nao subiu; veja {self.data / 'cli-engine.log'}")
            if status:
                status(f"iniciando o motor... {int(time.monotonic() - t0)}s")
            self.port = live_port(self.data)
            if self.port:
                return self.port
            time.sleep(0.5)
        raise CliError("o motor demorou demais para subir")

    def close(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           capture_output=True, creationflags=0x0800_0000)
        else:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except ValueError:
        d = None
    if isinstance(d, list):  # erro de validação do FastAPI
        d = "; ".join(str(x.get("msg", x)) for x in d)
    return str(d or f"HTTP {r.status_code}")


class Api:
    """A API do motor com a sessão do dono (cookies, como no navegador)."""

    def __init__(self, port: int, data: Path) -> None:
        self.data = data
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{port}/api",
                                 timeout=httpx.Timeout(30, read=None))

    def login(self) -> dict:
        try:
            token = (self.data / "cli-token").read_text(encoding="ascii").strip()
        except OSError:
            raise CliError("sem o token local (cli-token); reinicie o app para gerar") from None
        r = self.http.post("/auth/local", headers={LOCAL_TOKEN_HEADER: token})
        if r.status_code == 404:
            raise CliError("este motor nao tem login local; atualize o app")
        if r.status_code == 409:
            raise CliError("ainda nao ha conta: abra o app (`aiw ui`) e crie a sua")
        if r.status_code >= 400:
            raise CliError(f"login local falhou: {_detail(r)}")
        return r.json()

    def call(self, method: str, path: str, **kw) -> object:
        r = self.http.request(method, path, **kw)
        if r.status_code == 401:  # access token expirou no meio da sessao
            self.login()
            r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise CliError(_detail(r))
        return r.json() if r.content else None

    def stream(self, method: str, path: str, **kw) -> Iterator[dict]:
        """Eventos SSE da geracao (`data: {...}`), na ordem."""
        for attempt in (0, 1):
            with self.http.stream(method, path, **kw) as r:
                if r.status_code == 401 and attempt == 0:
                    self.login()
                    continue
                if r.status_code >= 400:
                    r.read()
                    raise CliError(_detail(r))
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except ValueError:
                            continue
                return
