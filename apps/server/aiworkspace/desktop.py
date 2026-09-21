"""App do desktop (Windows, sem Docker): interface + API numa porta só.

    uvicorn aiworkspace.desktop:app --port 41414

  /api/*          → a API (o prefixo sai antes de chegar nela, como o Caddy faz no Docker)
  /auth/callback  → callback fixo do login ChatGPT/Codex (chega pela porta 1455)
  resto           → a interface exportada como arquivos estáticos (NEXT_OUTPUT=export)

Um processo e uma porta, sem Node: a interface não usa nada de servidor, então o
build estático + este despachante substituem o container `web` inteiro. Mesma origem
para interface e API = sem CORS no caminho e cookie de sessão valendo em tudo.

Ambiente (o launcher Start-AIWorkspace.ps1 preenche):
  AIW_WEB_DIR  pasta do export (`out/` do Next)
  AIW_PORT     porta desta instância — usada pelo encaminhador da porta 1455
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse

from .main import app as api

logger = logging.getLogger(__name__)

# O Python no Windows lê os tipos MIME do REGISTRO, que em muita máquina marca `.js`
# como text/plain — e o navegador recusa executar script com esse tipo (tela branca).
for _tipo, _ext in (
    ("application/javascript", ".js"), ("application/javascript", ".mjs"),
    ("text/css", ".css"), ("application/json", ".json"), ("text/html", ".html"),
    ("application/manifest+json", ".webmanifest"), ("image/svg+xml", ".svg"),
    ("font/woff2", ".woff2"), ("image/png", ".png"), ("text/plain", ".txt"),
):
    mimetypes.add_type(_tipo, _ext)

# callback do cliente público do Codex: fixo em localhost:1455 pela OpenAI
CODEX_CALLBACK_PORT = 1455


class DesktopApp:
    def __init__(self, api_app, web_dir: str | os.PathLike | None, port: int | None) -> None:
        self.api = api_app
        self.web = Path(web_dir).resolve() if web_dir else None
        self.port = port
        self._proxy: asyncio.base_events.Server | None = None

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.api(scope, self._lifespan_receive(receive), send)
            return
        path = scope.get("path") or "/"
        if path == "/api" or path.startswith("/api/"):
            # igual ao `handle_path /api/*` do Caddy: a API nunca vê o prefixo
            novo = path[4:] or "/"
            scope = {**scope, "path": novo, "raw_path": novo.encode()}
            await self.api(scope, receive, send)
            return
        if path == "/auth/callback":
            await self.api(scope, receive, send)
            return
        if kind != "http":
            await send({"type": "websocket.close", "code": 1000})
            return
        await self._static(path, scope.get("query_string", b"").decode("latin-1"))(scope, receive, send)

    # ------------------------------------------------------------------ estático
    def _static(self, path: str, query: str):
        if self.web is None or not self.web.is_dir():
            return PlainTextResponse("interface não encontrada (AIW_WEB_DIR)", status_code=503)
        rel = path.lstrip("/")
        # links antigos de compartilhamento: /shared/<id> → /shared?id=<id>
        if rel.startswith("shared/") and rel.count("/") == 1 and len(rel) > len("shared/"):
            return RedirectResponse(f"/shared?id={quote(rel[len('shared/'):])}", status_code=308)
        for candidato in (rel or "index.html", f"{rel}.html", f"{rel.rstrip('/')}/index.html"):
            arquivo = self._inside(candidato)
            if arquivo is not None:
                imutavel = rel.startswith("_next/static/")
                return FileResponse(arquivo, headers={
                    "Cache-Control": "public, max-age=31536000, immutable" if imutavel else "no-cache",
                })
        nao_achou = self._inside("404.html")
        if nao_achou is not None:
            return FileResponse(nao_achou, status_code=404, headers={"Cache-Control": "no-cache"})
        return PlainTextResponse("não encontrado", status_code=404)

    def _inside(self, rel: str) -> Path | None:
        """O arquivo pedido, só se existir DENTRO da pasta da interface."""
        try:
            alvo = (self.web / rel).resolve()
        except (OSError, ValueError):
            return None
        if alvo != self.web and self.web not in alvo.parents:
            return None
        return alvo if alvo.is_file() else None

    # ------------------------------------------------------------------ lifespan
    def _lifespan_receive(self, receive):
        async def recv():
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await self._start_codex_proxy()
            elif msg["type"] == "lifespan.shutdown" and self._proxy is not None:
                self._proxy.close()
            return msg
        return recv

    async def _start_codex_proxy(self) -> None:
        """Encaminha localhost:1455 → esta porta. A OpenAI devolve o login do ChatGPT
        (plano B, quando o fluxo por código não está disponível) SEMPRE em
        localhost:1455/auth/callback; no Docker o compose publica essa porta, aqui
        quem escuta é este encaminhador. Porta ocupada = só o plano B fica sem rota."""
        if not self.port:
            return
        destino = self.port

        async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            try:
                while dados := await r.read(65536):
                    w.write(dados)
                    await w.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                w.close()

        async def conexao(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
            try:
                ur, uw = await asyncio.open_connection("127.0.0.1", destino)
            except OSError:
                cw.close()
                return
            await asyncio.gather(pipe(cr, uw), pipe(ur, cw), return_exceptions=True)

        try:
            self._proxy = await asyncio.start_server(conexao, "127.0.0.1", CODEX_CALLBACK_PORT)
        except OSError as exc:
            logger.warning("porta %s ocupada: callback do login ChatGPT indisponível (%s)",
                           CODEX_CALLBACK_PORT, exc)


def _porta() -> int | None:
    try:
        return int(os.environ.get("AIW_PORT") or 0) or None
    except ValueError:
        return None


app = DesktopApp(api, os.environ.get("AIW_WEB_DIR"), _porta())
