"""Navegador de pesquisa: o navegador do usuário (Brave/Chrome/Edge desta máquina) com
um perfil FIXO, usado como mecanismo de busca. Google primeiro (do IP de casa, com
perfil que volta, ele funciona); CAPTCHA faz a página descansar e a busca segue."""
from __future__ import annotations

from pathlib import Path

import pytest

from aiworkspace.search import providers
from aiworkspace.search.providers import SearchConfig
from aiworkspace.tools import browser_driver, sift_service
from aiworkspace.tools.browser_driver import LOCAL_SEARCH


@pytest.fixture(autouse=True)
def _limpo():
    providers._COOLING.clear()
    providers._STRIKES.clear()


def _extract_fake(monkeypatch, respostas):
    paginas = []

    def fake(endpoint, key, url, script):
        paginas.append((endpoint, url))
        for trecho, r in respostas.items():
            if trecho in url:
                return r
        return []

    monkeypatch.setattr(browser_driver.driver, "extract", fake)
    return paginas


_BOM = [{"title": "WebSockets - FastAPI", "url": "https://fastapi.tiangolo.com/ws", "content": "fastapi websocket"}]


def test_no_navegador_do_usuario_o_google_vem_primeiro(monkeypatch):
    paginas = _extract_fake(monkeypatch, {"google.com": _BOM})
    out = sift_service.browser_web_search(LOCAL_SEARCH, "fastapi websocket", 3)
    assert out[0]["url"] == "https://fastapi.tiangolo.com/ws"
    assert [u for _, u in paginas][0].startswith("https://www.google.com/search?q=fastapi")


def test_no_navegador_do_servidor_o_google_nem_entra(monkeypatch):
    paginas = _extract_fake(monkeypatch, {"duckduckgo": _BOM})
    sift_service.browser_web_search("ws://browserless", "fastapi websocket", 3)
    assert not any("google.com" in u for _, u in paginas)


def test_captcha_faz_a_pagina_descansar_e_segue_nas_outras(monkeypatch):
    paginas = _extract_fake(monkeypatch, {"google.com": {"blocked": True}, "bing.com": _BOM})
    out = sift_service.browser_web_search(LOCAL_SEARCH, "fastapi websocket", 3)
    assert out == _BOM[:3]
    assert providers._STRIKES.get("browser-www.google.com") == 1
    # a próxima busca nem abre o Google (descansando)
    paginas.clear()
    sift_service.browser_web_search(LOCAL_SEARCH, "fastapi websocket", 3)
    assert not any("google.com" in u for _, u in paginas)


def test_navegador_escolhido_usa_o_do_usuario(monkeypatch):
    monkeypatch.setattr(browser_driver, "find_user_browser", lambda: ("C:/brave.exe", "Brave"))
    paginas = _extract_fake(monkeypatch, {"google.com": _BOM})
    cfg = sift_service.with_browser_search(SearchConfig(provider="browser"), {"ws_url": "ws://servidor"})
    cfg.browser_search("fastapi websocket", 3)
    assert paginas[0][0] == LOCAL_SEARCH


def test_sem_navegador_no_computador_cai_no_do_servidor(monkeypatch):
    monkeypatch.setattr(browser_driver, "find_user_browser", lambda: None)
    paginas = _extract_fake(monkeypatch, {"duckduckgo": _BOM})
    cfg = sift_service.with_browser_search(SearchConfig(provider="browser"), {"ws_url": "ws://servidor"})
    cfg.browser_search("fastapi websocket", 3)
    assert paginas[0][0] == "ws://servidor"


def test_outros_mecanismos_so_usam_o_navegador_como_ultimo_recurso(monkeypatch):
    monkeypatch.setattr(browser_driver, "find_user_browser", lambda: ("C:/brave.exe", "Brave"))
    paginas = _extract_fake(monkeypatch, {"duckduckgo": _BOM})
    cfg = sift_service.with_browser_search(SearchConfig(provider="metasearch"), {"ws_url": "ws://servidor"})
    cfg.browser_search("fastapi websocket", 3)
    assert paginas[0][0] == "ws://servidor"  # não abre o navegador do usuário sem ele pedir
    assert sift_service.with_browser_search(SearchConfig(), {}).browser_search is None


def test_navegador_principal_cai_nos_motores_se_nao_achar():
    assert providers._fallbacks("browser", SearchConfig(provider="browser"))[0] == "metasearch"
    assert "browser" not in providers._fallbacks("browser", SearchConfig(provider="browser"))


def test_perfil_fixo_nao_e_apagado_ao_parar(tmp_path):
    perfil = tmp_path / "search-browser"
    perfil.mkdir()
    (perfil / "Cookies").write_text("x")
    lb = browser_driver._LocalBrowser(persistent_profile=str(perfil), user_browser=True)
    lb.profile = str(perfil)
    lb.stop()
    assert (perfil / "Cookies").exists() and lb.profile is None


def test_executavel_forcado_vale_para_o_navegador_do_usuario(monkeypatch, tmp_path):
    exe = tmp_path / "brave.exe"
    exe.write_text("")
    monkeypatch.setenv("BROWSER_EXECUTABLE", str(exe))
    assert browser_driver.find_user_browser() == (str(exe), "brave")
    monkeypatch.setenv("BROWSER_EXECUTABLE", str(Path(tmp_path) / "nao-existe.exe"))
    assert browser_driver.find_user_browser() is None
