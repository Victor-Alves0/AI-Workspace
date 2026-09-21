"""App do desktop: interface estática + API numa porta só, sem Node.

O `aiworkspace.desktop` substitui o container `web` (Node) e o Caddy no Windows: serve
os arquivos do `next build` com NEXT_OUTPUT=export e monta a API em /api. Os testes
travam o que o Caddy garante no Docker — a API nunca vê o prefixo — e o que o Node
fazia: achar a página certa, com o tipo certo, sem servir nada fora da pasta.
"""
from __future__ import annotations

import asyncio
import socket

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from aiworkspace import desktop


def _api_espia():
    """API falsa que devolve o caminho que recebeu — é o que interessa aqui."""
    async def eco(request):
        return JSONResponse({"path": request.url.path, "raw": request.scope["raw_path"].decode(),
                             "query": request.url.query})
    return Starlette(routes=[Route("/{resto:path}", eco, methods=["GET", "POST"])])


@pytest.fixture()
def web(tmp_path):
    raiz = tmp_path / "out"
    (raiz / "_next" / "static" / "chunks").mkdir(parents=True)
    (raiz / "index.html").write_text("<html>inicio</html>", encoding="utf-8")
    (raiz / "chat.html").write_text("<html>chat</html>", encoding="utf-8")
    (raiz / "chat.txt").write_text("rsc", encoding="utf-8")
    (raiz / "shared.html").write_text("<html>shared</html>", encoding="utf-8")
    (raiz / "404.html").write_text("<html>404</html>", encoding="utf-8")
    (raiz / "_next" / "static" / "chunks" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "segredo.txt").write_text("NÃO SAI", encoding="utf-8")
    return raiz


@pytest.fixture()
def cliente(web):
    with TestClient(desktop.DesktopApp(_api_espia(), web, None)) as c:
        yield c


def test_api_nao_ve_o_prefixo_como_atras_do_caddy(cliente):
    """No Docker o Caddy tira o /api (`handle_path`); limites de taxa e middlewares da
    API olham o caminho — se aqui ele chegasse com /api, o desktop se comportaria
    diferente do servidor."""
    r = cliente.get("/api/auth/login?x=1")

    assert r.json() == {"path": "/auth/login", "raw": "/auth/login", "query": "x=1"}
    assert cliente.get("/api").json()["path"] == "/"


def test_callback_do_login_chatgpt_chega_na_api(cliente):
    assert cliente.get("/auth/callback?code=c").json()["path"] == "/auth/callback"


@pytest.mark.parametrize("url,trecho", [
    ("/", "inicio"),
    ("/chat", "chat"),
    ("/chat?chatgpt=connected", "chat"),
    ("/shared?id=abc", "shared"),
])
def test_paginas_do_export(cliente, url, trecho):
    r = cliente.get(url)

    assert r.status_code == 200 and trecho in r.text
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"  # página nova após atualizar o app


def test_script_sai_como_javascript_e_imutavel(cliente):
    """No Windows o Python lê os tipos do registro, que às vezes marca .js como
    text/plain — e o navegador recusa o script: tela branca."""
    r = cliente.get("/_next/static/chunks/app.js")

    assert r.headers["content-type"].startswith("application/javascript")
    assert "immutable" in r.headers["cache-control"]


def test_dados_do_roteador_sao_servidos(cliente):
    assert cliente.get("/chat.txt").text == "rsc"


def test_link_antigo_de_compartilhamento_redireciona(cliente):
    r = cliente.get("/shared/abc123", follow_redirects=False)

    assert r.status_code == 308
    assert r.headers["location"] == "/shared?id=abc123"


@pytest.mark.parametrize("url", ["/../segredo.txt", "/..%2fsegredo.txt", "/%2e%2e/segredo.txt"])
def test_nada_fora_da_pasta_da_interface(cliente, url):
    r = cliente.get(url)

    assert "NÃO SAI" not in r.text
    assert r.status_code == 404


def test_pagina_inexistente_usa_o_404_do_export(cliente):
    r = cliente.get("/nao-existe")

    assert r.status_code == 404 and "404" in r.text


def test_sem_a_pasta_da_interface_avisa_em_vez_de_quebrar():
    with TestClient(desktop.DesktopApp(_api_espia(), None, None)) as c:
        assert c.get("/chat").status_code == 503
        assert c.get("/api/x").json()["path"] == "/x"  # a API continua de pé


# --------------------------------------------------------------------------- #
# Porta 1455 (login ChatGPT)                                                   #
# --------------------------------------------------------------------------- #
def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_encaminhador_da_1455_leva_ate_a_porta_do_app(monkeypatch):
    """A OpenAI devolve o login SEMPRE em localhost:1455; no Docker o compose publica
    a porta, no desktop quem escuta é o encaminhador."""
    destino = _porta_livre()
    entrada = _porta_livre()
    monkeypatch.setattr(desktop, "CODEX_CALLBACK_PORT", entrada)

    async def eco(r, w):
        dados = await r.read(100)
        w.write(b"ECO:" + dados)
        await w.drain()
        w.close()

    servidor = await asyncio.start_server(eco, "127.0.0.1", destino)
    app = desktop.DesktopApp(_api_espia(), None, destino)
    await app._start_codex_proxy()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", entrada)
        w.write(b"GET /auth/callback")
        await w.drain()
        resposta = await asyncio.wait_for(r.read(100), 5)
        w.close()
        assert resposta == b"ECO:GET /auth/callback"
    finally:
        app._proxy.close()
        servidor.close()


@pytest.mark.asyncio
async def test_1455_ocupada_nao_impede_o_app_de_subir(monkeypatch):
    ocupada = socket.socket()
    ocupada.bind(("127.0.0.1", 0))
    ocupada.listen()
    monkeypatch.setattr(desktop, "CODEX_CALLBACK_PORT", ocupada.getsockname()[1])
    try:
        app = desktop.DesktopApp(_api_espia(), None, 41414)
        await app._start_codex_proxy()  # não levanta
        assert app._proxy is None
    finally:
        ocupada.close()
