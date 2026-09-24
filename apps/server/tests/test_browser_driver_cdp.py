"""Navegador headless em CDP puro, contra um Edge/Chrome DE VERDADE.

O driver trocou o Playwright (~140 MB no motor) por CDP direto por WebSocket, e ganhou o
modo `local`: sobe o Edge/Chrome instalado — o caminho do app desktop no Windows. Estes
testes rodam cada ação num navegador real contra páginas servidas localmente; sem
navegador na máquina (CI Linux), pulam.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

import pytest

from aiworkspace.tools import browser_driver as bd

pytestmark = pytest.mark.skipif(bd.find_local_browser() is None, reason="sem Edge/Chrome nesta máquina")

PAGINAS = {
    "index.html": """<!doctype html><meta charset="utf-8"><title>Início</title>
<p id="msg">texto original</p>
<button onclick="document.getElementById('msg').innerText='texto mudou'">Mudar texto</button>
<a href="/form.html">Ir para o formulário</a>""",
    "form.html": """<!doctype html><meta charset="utf-8"><title>Formulário</title>
<form action="/result.html" method="get">
  <label for="nome">Seu nome</label>
  <input id="nome" name="q" oninput="document.getElementById('eco').innerText='eco:'+this.value">
</form>
<div id="eco"></div>""",
    "result.html": """<!doctype html><meta charset="utf-8"><title>Resultado</title>
<p id="r"></p><script>document.getElementById('r').innerText='recebi '+new URLSearchParams(location.search).get('q')</script>""",
    # a página (em 127.0.0.1) carrega uma imagem de OUTRO host (localhost): imagem não
    # depende de CORS, então só a guarda explica um erro de carregamento
    "guarda.html": """<!doctype html><meta charset="utf-8"><title>Guarda</title><p id="s">esperando</p>
<img src="http://localhost:PORTA/pixel.png"
     onload="document.getElementById('s').innerText='vazou'"
     onerror="document.getElementById('s').innerText='bloqueado'">""",
}

# PNG 1x1 válido
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d00000000"
    "49454e44ae426082"
)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    raiz: Path = tmp_path_factory.mktemp("site")
    (raiz / "pixel.png").write_bytes(PIXEL)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(raiz), **k)

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    porta = srv.server_address[1]
    for nome, html in PAGINAS.items():
        (raiz / nome).write_text(html.replace("PORTA", str(porta)), encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def driver():
    d = bd.BrowserDriver()
    yield d
    d.shutdown()
    assert d._local.profile is None  # perfil removido: o navegador foi encerrado


def test_goto_le_titulo_texto_e_elementos(driver, site):
    st = driver.goto(bd.LOCAL, "c1", f"{site}/index.html")

    assert st["title"] == "Início"
    assert "texto original" in st["text"]
    assert {"Mudar texto", "Ir para o formulário"} <= {e["text"] for e in st["elements"]}


def test_clique_sem_navegacao(driver, site):
    driver.goto(bd.LOCAL, "c1", f"{site}/index.html")

    st = driver.click(bd.LOCAL, "c1", "Mudar texto")

    assert "texto mudou" in st["text"]


def test_clique_que_navega_e_voltar(driver, site):
    driver.goto(bd.LOCAL, "c1", f"{site}/index.html")

    st = driver.click(bd.LOCAL, "c1", "Ir para o formulário")
    assert st["title"] == "Formulário"

    st = driver.back(bd.LOCAL, "c1")
    assert st["title"] == "Início"


def test_digitar_pelo_rotulo_dispara_os_eventos_da_pagina(driver, site):
    """insertText gera eventos de digitação reais — é o que React/Vue enxergam."""
    driver.goto(bd.LOCAL, "c1", f"{site}/form.html")

    st = driver.type(bd.LOCAL, "c1", "Seu nome", "Victor", False)
    assert "eco:Victor" in st["text"]

    # digitar de novo SUBSTITUI (como o fill do Playwright), não acumula
    st = driver.type(bd.LOCAL, "c1", "#nome", "Ana", False)
    assert "eco:Ana" in st["text"] and "VictorAna" not in st["text"]


def test_enter_envia_o_formulario(driver, site):
    driver.goto(bd.LOCAL, "c1", f"{site}/form.html")

    st = driver.type(bd.LOCAL, "c1", "Seu nome", "teste", True)

    assert st["title"] == "Resultado"
    assert "recebi teste" in st["text"]


def test_alvo_pelo_numero_da_lista(driver, site):
    """O número que a leitura mostra (`elements[].i`) serve de alvo para clicar e digitar
    — o mesmo elemento, pela mesma enumeração."""
    st = driver.goto(bd.LOCAL, "c1", f"{site}/index.html")
    botao = next(e["i"] for e in st["elements"] if e["text"] == "Mudar texto")
    assert "texto mudou" in driver.click(bd.LOCAL, "c1", str(botao))["text"]

    st = driver.goto(bd.LOCAL, "c1", f"{site}/form.html")
    campo = next(e["i"] for e in st["elements"] if e["tag"] == "input")
    assert "eco:Bia" in driver.type(bd.LOCAL, "c1", str(campo), "Bia", False)["text"]


def test_numero_que_nao_e_campo_ou_nao_existe(driver, site):
    st = driver.goto(bd.LOCAL, "c1", f"{site}/index.html")
    botao = next(e["i"] for e in st["elements"] if e["text"] == "Mudar texto")
    with pytest.raises(bd.CDPError, match="not a text field"):
        driver.type(bd.LOCAL, "c1", str(botao), "x", False)
    with pytest.raises(bd.CDPError, match="not found"):
        driver.click(bd.LOCAL, "c1", "99")


def test_screenshot_e_png(driver, site):
    driver.goto(bd.LOCAL, "c1", f"{site}/index.html")

    png = driver.screenshot(bd.LOCAL, "c1")

    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000


def test_conversas_nao_compartilham_aba(driver, site):
    driver.goto(bd.LOCAL, "a", f"{site}/index.html")
    driver.goto(bd.LOCAL, "b", f"{site}/form.html")

    assert driver.read(bd.LOCAL, "a")["title"] == "Início"
    assert driver.read(bd.LOCAL, "b")["title"] == "Formulário"


def test_alvo_inexistente_vira_erro_legivel(driver, site):
    driver.goto(bd.LOCAL, "c1", f"{site}/index.html")

    with pytest.raises(bd.CDPError, match="element not found"):
        driver.click(bd.LOCAL, "c1", "Botão que não existe")


def test_fechar_e_ler_pede_goto(driver, site):
    driver.goto(bd.LOCAL, "fecha", f"{site}/index.html")
    driver.close(bd.LOCAL, "fecha")

    with pytest.raises(bd.CDPError, match="goto"):
        driver.read(bd.LOCAL, "fecha")


def test_guarda_de_rede_barra_requisicao_da_propria_pagina(site):
    """O `goto` passa pela guarda da tool, mas a página carregada pode buscar outra
    coisa sozinha (fetch, img, redirect). No desktop isso é a rede da casa do usuário:
    toda requisição passa pela mesma guarda."""
    from urllib.parse import urlparse

    # a guarda real (_public_web_url) decide pelo HOST; o driver guarda a decisão por
    # host. Aqui "localhost" faz o papel do endereço da rede interna.
    d = bd.BrowserDriver()
    d.url_guard = lambda url: urlparse(url).hostname != "localhost"
    try:
        d.goto(bd.LOCAL, "g", f"{site}/guarda.html")
        import time
        texto = ""
        for _ in range(30):
            texto = d.read(bd.LOCAL, "g")["text"]
            if "esperando" not in texto:
                break
            time.sleep(0.1)
        assert "bloqueado" in texto
    finally:
        d.shutdown()


def test_sem_guarda_a_mesma_pagina_carrega(site):
    """Controle do teste acima: sem guarda, a imagem do outro host carrega."""
    import time

    d = bd.BrowserDriver()
    try:
        d.goto(bd.LOCAL, "g", f"{site}/guarda.html")
        texto = ""
        for _ in range(30):
            texto = d.read(bd.LOCAL, "g")["text"]
            if "esperando" not in texto:
                break
            time.sleep(0.1)
        assert "vazou" in texto
    finally:
        d.shutdown()
