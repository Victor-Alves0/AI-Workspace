"""Providers de pesquisa na web. Todos retornam uma lista normalizada de resultados.

Suportados:
  - metasearch  (sem chave, padrão) — metabusca DENTRO do processo (lib ddgs): consulta
                em paralelo Bing, Brave, DuckDuckGo, Google, Mojeek, Startpage, Yahoo,
                Yandex e Wikipedia, junta, tira duplicados e ordena. Faz o papel que o
                SearXNG fazia, sem container.
  - tavily      (chave de API)
  - brave       (chave de API)

`duckduckgo` e `searxng` continuam aceitos (preferências antigas): o primeiro vira a
metabusca só com o DuckDuckGo; o segundo, a metabusca completa.

A escolha do provider e as chaves vêm de `SearchConfig` (montada por chamada,
considerando env global + segredos do usuário).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import httpx
from fastapi.concurrency import run_in_threadpool


class SearchResult(TypedDict):
    title: str
    url: str
    content: str


@dataclass
class SearchConfig:
    provider: str = "metasearch"
    max_results: int = 5
    # metabusca: motores da ddgs separados por vírgula ("auto" = todos) e região
    # ("wt-wt" = sem região; "br-pt" prioriza resultados do Brasil)
    engines: str = "auto"
    region: str = "wt-wt"
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    # multi-mecanismo: consulta vários e mescla os resultados (dedup por URL)
    providers: tuple[str, ...] = ()
    multi: bool = False
    # domínios a EXCLUIR dos resultados (separados por vírgula na UI)
    domain_filter: tuple[str, ...] = ()


class SearchProviderError(Exception):
    """O provider respondeu, mas não tem como entregar resultado — e o motivo importa
    (ex.: todos os motores da metabusca barrados por CAPTCHA). Sem isto o motivo
    virava "nenhum resultado" e ninguém sabia o que consertar."""


_PROVIDERS = {
    "metasearch": lambda q, c: _metasearch(q, c),
    "tavily": lambda q, c: _tavily(q, c),
    "brave": lambda q, c: _brave(q, c),
    # preferências antigas
    "duckduckgo": lambda q, c: _metasearch(q, c, engines="duckduckgo"),
    "searxng": lambda q, c: _metasearch(q, c),
}


def _blocked(url: str, domains: tuple[str, ...]) -> bool:
    if not domains or not url:
        return False
    u = url.lower()
    return any(d and d.lower() in u for d in domains)


async def _one(
    provider: str, query: str, cfg: SearchConfig
) -> tuple[list[SearchResult], str | None]:
    fn = _PROVIDERS.get((provider or "").lower(), _PROVIDERS["metasearch"])
    try:
        return await fn(query, cfg), None
    except Exception as exc:  # noqa: BLE001 - um provider falho não derruba a busca
        motivo = str(exc).strip() or type(exc).__name__
        return [], f"{provider}: {motivo[:300]}"


async def web_search(query: str, cfg: SearchConfig) -> list[SearchResult]:
    results, _ = await web_search_detailed(query, cfg)
    return results


async def web_search_detailed(
    query: str, cfg: SearchConfig
) -> tuple[list[SearchResult], list[str]]:
    """Como `web_search`, mais o motivo de cada provider que falhou — para o teste de
    conexão e a tool dizerem POR QUE veio vazio."""
    # define a lista de mecanismos a consultar
    if cfg.multi and cfg.providers:
        provs = list(dict.fromkeys(p.lower() for p in cfg.providers if p)) or ["metasearch"]
    else:
        provs = [(cfg.provider or "metasearch").lower()]

    results: list[SearchResult] = []
    errors: list[str] = []
    for p in provs:
        found, err = await _one(p, query, cfg)
        results.extend(found)
        if err:
            errors.append(err)

    # filtro de domínio + dedup por URL, preservando a ordem
    out: list[SearchResult] = []
    seen: set[str] = set()
    for r in results:
        url = r.get("url") or ""
        if _blocked(url, cfg.domain_filter):
            continue
        key = url or (r.get("title") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= max(1, cfg.max_results):
            break
    return out, errors


async def _metasearch(query: str, cfg: SearchConfig, engines: str | None = None) -> list[SearchResult]:
    """Metabusca da ddgs: dispara vários motores em paralelo e agrega/ordena."""
    def _run() -> list[SearchResult]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        try:
            found = DDGS().text(
                query,
                region=cfg.region or "wt-wt",
                safesearch="moderate",
                max_results=cfg.max_results,
                backend=(engines or cfg.engines or "auto"),
            )
        except DDGSException as exc:
            # "No results found" com todos os motores falhando (ex.: CAPTCHA de IP de
            # servidor): o motivo precisa chegar ao teste de conexão e à tool
            raise SearchProviderError(f"metabusca sem resultado: {exc}") from exc
        return [
            {"title": r.get("title", ""), "url": r.get("href", r.get("url", "")), "content": r.get("body", "")}
            for r in found or []
        ]

    return await run_in_threadpool(_run)


async def _tavily(query: str, cfg: SearchConfig) -> list[SearchResult]:
    if not cfg.tavily_api_key:
        return [{"title": "Erro", "url": "", "content": "Configure a chave do Tavily"}]
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": cfg.tavily_api_key,
                "query": query,
                "max_results": cfg.max_results,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in data.get("results", [])
    ]


async def _brave(query: str, cfg: SearchConfig) -> list[SearchResult]:
    if not cfg.brave_api_key:
        return [{"title": "Erro", "url": "", "content": "Configure a chave do Brave"}]
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": cfg.max_results},
            headers={"X-Subscription-Token": cfg.brave_api_key, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("description", ""),
        }
        for r in data.get("web", {}).get("results", [])[: cfg.max_results]
    ]
