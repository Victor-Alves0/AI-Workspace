"""Pesquisa na web: a metabusca embutida (ddgs) substituiu o SearXNG. Quando todos os
motores falham (CAPTCHA de IP de servidor é o caso clássico), o MOTIVO precisa chegar
ao teste de conexão e à tool — "nenhum resultado" sozinho não diz o que consertar."""
from __future__ import annotations

import pytest

from aiworkspace.search import providers
from aiworkspace.search.providers import SearchConfig, web_search_detailed


class _FakeDDGS:
    chamadas: list[dict] = []
    resposta: list | Exception = []

    def text(self, query, **kw):
        _FakeDDGS.chamadas.append({"query": query, **kw})
        if isinstance(_FakeDDGS.resposta, Exception):
            raise _FakeDDGS.resposta
        return _FakeDDGS.resposta


@pytest.fixture
def ddgs(monkeypatch):
    import ddgs as mod

    _FakeDDGS.chamadas = []
    _FakeDDGS.resposta = []
    monkeypatch.setattr(mod, "DDGS", _FakeDDGS)
    return _FakeDDGS


async def test_normaliza_resultados_e_repassa_motores_e_regiao(ddgs):
    ddgs.resposta = [{"title": "T", "href": "https://a.com", "body": "B"}]
    cfg = SearchConfig(engines="bing,brave", region="br-pt", max_results=3)

    results, errors = await web_search_detailed("gatos", cfg)

    assert results == [{"title": "T", "url": "https://a.com", "content": "B"}] and errors == []
    assert ddgs.chamadas[0]["backend"] == "bing,brave" and ddgs.chamadas[0]["region"] == "br-pt"


@pytest.mark.parametrize("antigo,motores", [("duckduckgo", "duckduckgo"), ("searxng", "auto")])
async def test_preferencias_antigas_viram_metabusca(ddgs, antigo, motores):
    ddgs.resposta = [{"title": "x", "href": "https://x.com", "body": ""}]

    results, _ = await web_search_detailed("q", SearchConfig(provider=antigo))

    assert len(results) == 1 and ddgs.chamadas[0]["backend"] == motores


async def test_todos_os_motores_falhando_explica_o_motivo(ddgs):
    from ddgs.exceptions import DDGSException

    ddgs.resposta = DDGSException("No results found.")

    results, errors = await web_search_detailed("q", SearchConfig())

    assert results == []
    assert errors and "metasearch" in errors[0] and "No results found" in errors[0]


async def test_vazio_de_verdade_nao_inventa_erro(ddgs):
    results, errors = await web_search_detailed("q", SearchConfig())
    assert results == [] and errors == []


def test_padrao_e_a_metabusca():
    from aiworkspace.config import get_settings

    assert SearchConfig().provider == "metasearch"
    assert get_settings().web_search_provider == "metasearch"
    assert "metasearch" in providers._PROVIDERS
