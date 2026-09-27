"""Pesquisa na web: a metabusca embutida (ddgs) substituiu o SearXNG. Quando todos os
motores falham (CAPTCHA de IP de servidor é o caso clássico), o MOTIVO precisa chegar
ao teste de conexão e à tool — "nenhum resultado" sozinho não diz o que consertar."""
from __future__ import annotations

import pytest

from aiworkspace.search import providers
from aiworkspace.search.providers import SearchConfig, web_search_detailed


class _FakeDDGS:
    chamadas: list[dict] = []
    # uma resposta fixa, ou uma lista de respostas por chamada (a última se repete)
    resposta: list | Exception = []
    roteiro: list | None = None
    demora = 0.0

    def __init__(self, **kw):
        pass

    def text(self, query, **kw):
        import time as _t
        _FakeDDGS.chamadas.append({"query": query, **kw})
        if _FakeDDGS.demora:
            _t.sleep(_FakeDDGS.demora)
        r = _FakeDDGS.resposta
        if _FakeDDGS.roteiro:
            r = _FakeDDGS.roteiro[min(len(_FakeDDGS.chamadas), len(_FakeDDGS.roteiro)) - 1]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _estado_limpo(monkeypatch):
    """cache, voo único e descanso de motores são globais do processo"""
    providers._CACHE.clear()
    providers._INFLIGHT.clear()
    providers._COOLING.clear()
    monkeypatch.setattr(providers, "_RETRY_BASE", 0.0)


@pytest.fixture
def ddgs(monkeypatch):
    import ddgs as mod

    _FakeDDGS.chamadas = []
    _FakeDDGS.resposta = []
    _FakeDDGS.roteiro = None
    _FakeDDGS.demora = 0.0
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

    backend = ddgs.chamadas[0]["backend"]
    if motores == "auto":
        # metabusca completa: todos os motores, menos a Wikipedia sem região (wt.wikipedia.org não existe)
        usados = set(backend.split(","))
        assert {"duckduckgo", "google"} <= usados and "wikipedia" not in usados
    else:
        assert backend == motores
    assert len(results) == 1


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


# ------------------------------ resiliência --------------------------------------
_OK = [{"title": "T", "href": "https://a.com", "body": "B"}]


async def test_bloqueio_passageiro_tenta_de_novo(ddgs):
    from ddgs.exceptions import RatelimitException

    ddgs.roteiro = [RatelimitException("429 Too Many Requests"), _OK]
    results, errors = await web_search_detailed("gatos", SearchConfig())
    assert results and errors == [] and len(ddgs.chamadas) == 2


async def test_busca_repetida_sai_do_cache(ddgs):
    ddgs.resposta = _OK
    await web_search_detailed("Gatos  pretos", SearchConfig())
    results, _ = await web_search_detailed("gatos pretos", SearchConfig())
    assert results and len(ddgs.chamadas) == 1


def test_muitos_agentes_com_a_mesma_busca_fazem_uma_ida_so(ddgs):
    """Como a tool roda: cada chamada numa thread, com o próprio asyncio.run."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    ddgs.resposta = _OK
    ddgs.demora = 0.4
    with ThreadPoolExecutor(12) as ex:
        saidas = list(ex.map(lambda _: asyncio.run(web_search_detailed("gta 6", SearchConfig())), range(12)))
    assert all(r for r, _ in saidas)
    assert len(ddgs.chamadas) == 1


async def test_motor_bloqueado_descansa_e_sai_das_proximas_buscas(ddgs):
    import logging

    providers._install_engine_watch()
    logging.getLogger("ddgs.ddgs").info("Error in engine %s: %r", "google", Exception("403 captcha"))
    ddgs.resposta = _OK
    await web_search_detailed("q1", SearchConfig())
    backend = ddgs.chamadas[0]["backend"]
    assert "google" not in backend.split(",") and "duckduckgo" in backend.split(",")


async def test_metabusca_esgotada_cai_no_tavily_quando_ha_chave(ddgs, monkeypatch):
    from ddgs.exceptions import DDGSException

    ddgs.resposta = DDGSException("No results found.")

    async def _tav(q, cfg):
        return [{"title": "Tav", "url": "https://t.com", "content": ""}]

    monkeypatch.setattr(providers, "_tavily", _tav)
    results, errors = await web_search_detailed("q", SearchConfig(tavily_api_key="k"))
    assert [r["title"] for r in results] == ["Tav"] and errors == []
    assert len(ddgs.chamadas) == providers._ATTEMPTS


async def test_tavily_sem_chave_usa_a_metabusca(ddgs):
    ddgs.resposta = _OK
    results, errors = await web_search_detailed("q", SearchConfig(provider="tavily"))
    assert results and results[0]["url"] == "https://a.com" and errors == []


async def test_motores_barrados_caem_na_busca_pelo_navegador(ddgs):
    """Sem chave de API e com os motores barrando o IP, o último recurso é a página de
    resultados aberta no navegador headless."""
    from ddgs.exceptions import DDGSException

    ddgs.resposta = DDGSException("error sending request > 403 captcha")
    pedidos = []

    def _nav(q, n):
        pedidos.append((q, n))
        return [{"title": "Pelo navegador", "url": "https://n.com", "content": "x"}]

    results, errors = await web_search_detailed("q", SearchConfig(browser_search=_nav, max_results=4))
    assert [r["title"] for r in results] == ["Pelo navegador"] and errors == []
    assert pedidos == [("q", 4)]


async def test_sem_navegador_e_sem_chave_explica_tudo_que_tentou(ddgs):
    from ddgs.exceptions import DDGSException

    ddgs.resposta = DDGSException("403 captcha")
    results, errors = await web_search_detailed("q", SearchConfig())
    assert results == [] and errors and "403 captcha" in errors[0]
