"""Providers de pesquisa na web. Todos retornam uma lista normalizada de resultados.

Suportados:
  - duckduckgo  (sem chave, padrão — via lib ddgs)
  - searxng     (self-hosted, sem chave — JSON API)
  - tavily      (chave de API)
  - brave       (chave de API)

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
    provider: str = "duckduckgo"
    max_results: int = 5
    searxng_url: str = "http://localhost:8080"
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    # multi-mecanismo: consulta vários e mescla os resultados (dedup por URL)
    providers: tuple[str, ...] = ()
    multi: bool = False
    # domínios a EXCLUIR dos resultados (separados por vírgula na UI)
    domain_filter: tuple[str, ...] = ()


_PROVIDERS = {
    "searxng": lambda q, c: _searxng(q, c),
    "tavily": lambda q, c: _tavily(q, c),
    "brave": lambda q, c: _brave(q, c),
    "duckduckgo": lambda q, c: _duckduckgo(q, c),
}


def _blocked(url: str, domains: tuple[str, ...]) -> bool:
    if not domains or not url:
        return False
    u = url.lower()
    return any(d and d.lower() in u for d in domains)


async def _one(provider: str, query: str, cfg: SearchConfig) -> list[SearchResult]:
    fn = _PROVIDERS.get((provider or "").lower(), _PROVIDERS["duckduckgo"])
    try:
        return await fn(query, cfg)
    except Exception:  # noqa: BLE001 - um provider falho não derruba a busca
        return []


async def web_search(query: str, cfg: SearchConfig) -> list[SearchResult]:
    # define a lista de mecanismos a consultar
    if cfg.multi and cfg.providers:
        provs = list(dict.fromkeys(p.lower() for p in cfg.providers if p)) or ["duckduckgo"]
    else:
        provs = [(cfg.provider or "duckduckgo").lower()]

    results: list[SearchResult] = []
    for p in provs:
        results.extend(await _one(p, query, cfg))

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
    return out


async def _duckduckgo(query: str, cfg: SearchConfig) -> list[SearchResult]:
    def _run() -> list[SearchResult]:
        from ddgs import DDGS

        out: list[SearchResult] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=cfg.max_results):
                out.append(
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", r.get("url", "")),
                        "content": r.get("body", ""),
                    }
                )
        return out

    return await run_in_threadpool(_run)


async def _searxng(query: str, cfg: SearchConfig) -> list[SearchResult]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{cfg.searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "safesearch": 1},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in data.get("results", [])[: cfg.max_results]
    ]


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
