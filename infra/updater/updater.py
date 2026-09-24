"""Updater do AI Workspace: o ÚNICO container com o socket do Docker.

O servidor do app executa código decidido pela IA (run_code, Codespace, navegador) —
por isso ele NUNCA recebe o socket (seria root no host). Este container não roda nada
disso: expõe uma ação só ("atualizar para o último commit do projeto"), na rede
interna `updater`, autenticada por um token que ele mesmo gera ao subir e grava no
volume `updater_state` (o servidor monta esse volume só-leitura).

  POST /update   → dispara run-update.sh em segundo plano (409 se já há um rodando)
  GET  /status   → {"state": idle|running|done|failed, "at", "log"}

Stdlib pura (sem dependências): http.server + subprocess.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = Path(os.environ.get("UPDATER_STATE_DIR", "/run/updater"))
TOKEN_FILE = STATE / "token"
STATUS_FILE = STATE / "status.json"
SCRIPT = os.environ.get("UPDATER_SCRIPT", "/opt/updater/run-update.sh")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status(state: str, log: str = "") -> None:
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"state": state, "at": _now(), "log": log[-4000:]}), encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(STATUS_FILE)


def _read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle"}


def _token() -> str:
    """Gera o token na 1ª subida; o servidor lê pelo volume compartilhado."""
    STATE.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    os.chmod(TOKEN_FILE, 0o644)  # o servidor roda como outro usuário (uid 10001)
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _run() -> None:
    try:
        proc = subprocess.run([SCRIPT], capture_output=True, text=True, timeout=3600)
        log = (proc.stdout or "") + (proc.stderr or "")
        # o script grava "done" ANTES de recriar este container (senão morreria no meio);
        # aqui só registramos a falha
        if proc.returncode != 0:
            _write_status("failed", log)
    except Exception as exc:  # noqa: BLE001
        _write_status("failed", str(exc))
    finally:
        _lock.release()


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(got) and hmac.compare_digest(got, TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True})
        elif self.path == "/status" and self._authorized():
            self._json(200, _read_status())
        else:
            self._json(404 if self._authorized() else 401, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path != "/update":
            self._json(404, {"error": "not found"})
            return
        if not _lock.acquire(blocking=False):
            self._json(409, {"error": "já há uma atualização em andamento"})
            return
        _write_status("running")
        threading.Thread(target=_run, daemon=True).start()
        self._json(202, {"ok": True})

    def log_message(self, fmt: str, *args) -> None:
        print(f"updater: {self.address_string()} {fmt % args}", flush=True)


TOKEN = _token()
if _read_status().get("state") == "running":
    # o script grava "done" ANTES de recriar este container; subir com "running" quer
    # dizer que a atualização foi interrompida no meio (reboot, OOM, docker restart)
    _write_status("failed", "atualização interrompida (o updater reiniciou no meio)")

if __name__ == "__main__":
    print("updater: ouvindo em :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
