"""Busca vazia POR FALHA precisa dizer o motivo.

Caso real (19/09): o SearXNG estava no ar, mas DuckDuckGo/Google devolviam CAPTCHA ao
IP do servidor. O teste de conexão dizia só "Nenhum resultado retornado" e a tool
devolvia lista vazia — o modelo concluía que "não existe nada sobre isso".
"""
from __future__ import annotations

import httpx
import pytest

from aiworkspace.search import providers
from aiworkspace.search.providers import SearchConfig, web_search, web_search_detailed


def _searxng_responde(monkeypatch, payload: dict) -> None:
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
        return real(*args, **kwargs)

    monkeypatch.setattr(providers.httpx, "AsyncClient", fake)


_CFG = SearchConfig(provider="searxng", searxng_url="http://searxng:8080")


async def test_searxng_com_motores_barrados_explica_quais_e_por_que(monkeypatch):
    _searxng_responde(monkeypatch, {
        "results": [],
        "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["google", "too many requests"]],
    })
    results, errors = await web_search_detailed("x", _CFG)
    assert results == []
    assert len(errors) == 1
    assert "duckduckgo (CAPTCHA)" in errors[0]
    assert "google (too many requests)" in errors[0]


async def test_searxng_vazio_de_verdade_nao_inventa_erro(monkeypatch):
    _searxng_responde(monkeypatch, {"results": [], "unresponsive_engines": []})
    assert await web_search_detailed("x", _CFG) == ([], [])


async def test_resultado_parcial_ignora_motor_que_falhou(monkeypatch):
    """Um motor com CAPTCHA e outro funcionando: há resultado, então não é erro."""
    _searxng_responde(monkeypatch, {
        "results": [{"title": "T", "url": "https://a.example", "content": "c"}],
        "unresponsive_engines": [["duckduckgo", "CAPTCHA"]],
    })
    results, errors = await web_search_detailed("x", _CFG)
    assert [r["url"] for r in results] == ["https://a.example"]
    assert errors == []


async def test_web_search_continua_devolvendo_lista(monkeypatch):
    """Contrato dos 6 chamadores antigos (deep search, finanças, automações...)."""
    _searxng_responde(monkeypatch, {"results": [], "unresponsive_engines": [["bing", "timeout"]]})
    assert await web_search("x", _CFG) == []


@pytest.mark.parametrize("item,esperado", [
    (["duckduckgo", "CAPTCHA"], "duckduckgo (CAPTCHA)"),
    (["brave", ""], "brave"),
    ("mojeek", "mojeek"),
])
def test_formato_dos_motores_sem_resposta(item, esperado):
    assert providers._searxng_failures({"unresponsive_engines": [item]}) == [esperado]
