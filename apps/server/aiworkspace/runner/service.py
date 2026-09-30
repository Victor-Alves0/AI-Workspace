"""Executor isolado: o ÚNICO lugar onde roda código decidido pela IA no perfil servidor.

Roda num container próprio (`runner` no docker-compose), com a mesma imagem do server,
mas SEM nada que valha a pena roubar: nenhum segredo no ambiente, nenhum arquivo de
segredo montado, só o volume dos projetos (`/data/codespace`), sem rota para o banco nem
para os outros serviços, sistema de arquivos somente-leitura, sem capabilities. Uma
injeção de prompt que vire comando encontra aqui só os projetos — não o APP_SECRET, não
o banco, não os tokens do Google/GitHub/WhatsApp de ninguém.

Protocolo (um WebSocket por processo, em /proc; Bearer = token compartilhado):
  cliente → {"shell": "cmd"} ou {"argv": [...]}, + cwd, env, cpu_seconds, mem_mb,
            stdin (bool), merge_stderr (bool)
  runner  → {"t":"started","pid":N} · {"t":"out","d":texto} · {"t":"err","d":texto}
            · {"t":"exit","code":N}
  cliente → {"t":"in","d":texto} · {"t":"eof"} · {"t":"kill"}
Fechar o WebSocket mata a árvore do processo (o dono sumiu).

O token nasce aqui no primeiro boot (volume `runner_state`), e o server o lê do mesmo
volume em modo leitura — como o token do updater.
"""
from __future__ import annotations

import asyncio
import codecs
import hmac
import json
import logging
import os
import secrets
import signal
from http import HTTPStatus
from pathlib import Path
from typing import Any

logger = logging.getLogger("aiworkspace.runner")

_SECRET_HINTS = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "API_KEY",
                 "APIKEY", "PRIVATE", "DATABASE_URL", "DSN")
_MAX_PROCS = int(os.environ.get("RUNNER_MAX_PROCS", "64"))
_CHUNK = 64 * 1024
_live: set[int] = set()


def _roots() -> list[Path]:
    raw = os.environ.get("RUNNER_ROOTS", os.pathsep.join(("/data/codespace", "/tmp")))
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def _inside(path: Path) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    return any(rp == r or r in rp.parents for r in _roots())


def load_token(path: str) -> str:
    """Lê o token; na primeira vez, cria (atômico, 0600)."""
    p = Path(path)
    try:
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok)
    os.replace(tmp, p)
    return tok


def _env(extra: dict[str, Any] | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not any(h in k.upper() for h in _SECRET_HINTS) and not k.startswith("RUNNER_")}
    env["AIWORKSPACE_SANDBOX"] = "1"
    env.setdefault("CI", "1")
    for k, v in (extra or {}).items():
        env[str(k)] = str(v)
    return env


def _preexec(cpu_seconds: int, mem_mb: int):
    def _apply() -> None:  # pragma: no cover - roda no filho
        os.setsid()  # grupo próprio: matar a árvore inteira de uma vez
        os.umask(0o022)
        try:
            import resource
            if cpu_seconds > 0:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
            if mem_mb > 0:
                lim = mem_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
        except Exception:
            pass
    return _apply


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def _pump(stream: asyncio.StreamReader, ws: Any, kind: str) -> None:
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = await stream.read(_CHUNK)
        if not chunk:
            tail = dec.decode(b"", final=True)
            if tail:
                await ws.send(json.dumps({"t": kind, "d": tail}))
            return
        text = dec.decode(chunk)
        if text:
            await ws.send(json.dumps({"t": kind, "d": text}))


async def handle(ws: Any) -> None:
    if len(_live) >= _MAX_PROCS:
        await ws.close(1013, "limite de processos simultâneos")
        return
    try:
        spec = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    except Exception:  # noqa: BLE001
        await ws.close(1002, "especificação inválida")
        return
    cwd = Path(str(spec.get("cwd") or "/tmp"))
    if not _inside(cwd) or not cwd.is_dir():
        await ws.send(json.dumps({"t": "error", "d": f"pasta fora da área do executor ou inexistente: {cwd}"}))
        await ws.close()
        return
    if spec.get("shell"):
        args = ["/bin/sh", "-c", str(spec["shell"])]
    elif isinstance(spec.get("argv"), list) and spec["argv"]:
        args = [str(a) for a in spec["argv"]]
    else:
        await ws.send(json.dumps({"t": "error", "d": "informe shell ou argv"}))
        await ws.close()
        return
    merge = bool(spec.get("merge_stderr", True))
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(cwd), env=_env(spec.get("env")),
            stdin=asyncio.subprocess.PIPE if spec.get("stdin") else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if merge else asyncio.subprocess.PIPE,
            preexec_fn=_preexec(int(spec.get("cpu_seconds") or 0), int(spec.get("mem_mb") or 0)),
            limit=_CHUNK * 4,
        )
    except (OSError, ValueError) as exc:
        await ws.send(json.dumps({"t": "error", "d": f"não consegui iniciar: {exc}"}))
        await ws.close()
        return
    _live.add(proc.pid)
    await ws.send(json.dumps({"t": "started", "pid": proc.pid}))
    pumps = [asyncio.create_task(_pump(proc.stdout, ws, "out"))]
    if not merge:
        pumps.append(asyncio.create_task(_pump(proc.stderr, ws, "err")))

    async def _inbox() -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            t = msg.get("t")
            if t == "in" and proc.stdin is not None:
                try:
                    proc.stdin.write(str(msg.get("d") or "").encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif t == "eof" and proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            elif t == "kill":
                _killpg(proc.pid)

    inbox = asyncio.create_task(_inbox())
    try:
        waiter = asyncio.create_task(proc.wait())
        done, _ = await asyncio.wait({waiter, inbox}, return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            # o cliente foi embora (WebSocket fechou): mata a árvore e sai
            _killpg(proc.pid)
            await waiter
            return
        # saiu: um neto que herdou a saída (daemon largado) seguraria o pipe aberto
        # para sempre — dá um respiro para a saída chegar e derruba o resto do grupo
        try:
            await asyncio.wait_for(asyncio.gather(*pumps), timeout=5)
        except asyncio.TimeoutError:
            _killpg(proc.pid)
            await asyncio.gather(*pumps, return_exceptions=True)
        await ws.send(json.dumps({"t": "exit", "code": proc.returncode}))
    except Exception:  # noqa: BLE001 - conexão caiu no meio: não deixa órfão
        _killpg(proc.pid)
    finally:
        _live.discard(proc.pid)
        inbox.cancel()
        for p in pumps:
            p.cancel()
        _killpg(proc.pid)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


def _authorized(headers: Any, token: str) -> bool:
    got = str(headers.get("Authorization") or "")
    return got.startswith("Bearer ") and hmac.compare_digest(got[7:].strip(), token)


async def serve(host: str, port: int, token: str) -> None:
    from websockets.asyncio.server import serve as ws_serve

    def _process_request(connection: Any, request: Any):
        if request.path == "/health":
            return connection.respond(HTTPStatus.OK, "ok\n")
        if request.path != "/proc":
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        if not _authorized(request.headers, token):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return None

    async with ws_serve(handle, host, port, process_request=_process_request,
                        max_size=32 * 1024 * 1024, ping_interval=20, ping_timeout=60):
        logger.info("executor isolado ouvindo em %s:%s (raízes: %s)", host, port,
                    ", ".join(str(r) for r in _roots()))
        await asyncio.Future()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    token = load_token(os.environ.get("RUNNER_TOKEN_FILE", "/run/runner/token"))
    asyncio.run(serve(os.environ.get("RUNNER_HOST", "0.0.0.0"),
                      int(os.environ.get("RUNNER_PORT", "8765")), token))
