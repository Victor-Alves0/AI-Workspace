"""Ler Página: detecta quando o HTML cru não traz o conteúdo (precisa de JavaScript)."""
from aiworkspace.tools import sift_service as s


def test_app_de_javascript_precisa_de_render():
    html = '<html><div id="root"></div><script src="a.js"></script><script>x()</script>' + " " * 2000
    assert s._needs_js(html, "") is True


def test_aviso_de_habilitar_javascript():
    txt = "You need to enable JavaScript to run this app."
    assert s._needs_js(f"<noscript>{txt}</noscript>", txt) is True
    assert s._needs_js("<p>Habilite o JavaScript para continuar</p>", "Habilite o JavaScript para continuar") is True


def test_desafio_anti_bot():
    html = "<title>Just a moment...</title><script src=/cdn-cgi/challenge-platform/x.js></script>"
    assert s._needs_js(html, "Just a moment...") is True


def test_pagina_normal_nao_renderiza():
    texto = "Artigo com bastante texto legível. " * 60
    assert s._needs_js(f"<article>{texto}</article><script>a()</script><script>b()</script>", texto) is False
