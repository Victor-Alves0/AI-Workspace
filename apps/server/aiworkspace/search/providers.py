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

Resiliência (muitos agentes pesquisando ao mesmo tempo — antes a busca "morria":
os motores respondiam CAPTCHA/429 e a chamada voltava vazia):
  - poucas metabuscas simultâneas, num pool de threads PRÓPRIO: o excesso espera na
    fila em vez de martelar os motores (e não rouba as threads do resto do app);
  - cache curto + voo único: agentes com a mesma busca dividem UMA ida aos motores;
  - motor que respondeu bloqueio (CAPTCHA/429/403/timeout) descansa alguns minutos
    e as próximas buscas usam os outros;
  - até 3 tentativas com espera curta, trocando de motores;
  - fallback entre providers: metabusca esgotada → Tavily/Brave (se houver chave), e
    Tavily/Brave com erro → metabusca;
  - teto de tempo por busca, bem abaixo do watchdog das tools.

Relevância (o Yahoo, com busca cheia de aspas/operadores como `app="x"`, devolve
resultados ALEATÓRIOS — férias de verão, Pornhub, fotógrafo de Las Vegas — e, como os
outros motores não achavam nada, o lixo era o único resultado):
  - resultado sem NENHUMA palavra significativa da busca (título, trecho ou URL) é
    descartado; se nada sobra, o provider conta como falho e o próximo é tentado;
  - sem nada relevante, repete com a busca SIMPLIFICADA (sem aspas nem `chave=`).
A tool chama isto de dentro de `asyncio.run` numa thread (um loop por chamada), então
tudo aqui é thread-safe e independe de event loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import threading
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TypedDict

import httpx

logger = logging.getLogger(__name__)


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
    # último recurso: busca pela página de resultados no navegador headless
    # (query, max_results) → resultados. Injetada pela tool quando há navegador.
    browser_search: Callable[[str, int], list] | None = None


class SearchProviderError(Exception):
    """O provider respondeu, mas não tem como entregar resultado — e o motivo importa
    (ex.: todos os motores da metabusca barrados por CAPTCHA). Sem isto o motivo
    virava "nenhum resultado" e ninguém sabia o que consertar."""


_PROVIDERS = {
    "metasearch": lambda q, c: _metasearch(q, c),
    "tavily": lambda q, c: _tavily(q, c),
    "brave": lambda q, c: _brave(q, c),
    "browser": lambda q, c: _browser(q, c),
    # preferências antigas
    "duckduckgo": lambda q, c: _metasearch(q, c, engines="duckduckgo"),
    "searxng": lambda q, c: _metasearch(q, c),
}


def _blocked(url: str, domains: tuple[str, ...]) -> bool:
    if not domains or not url:
        return False
    u = url.lower()
    return any(d and d.lower() in u for d in domains)


# --------------------------------------------------------------------------- #
# Resiliência                                                                 #
# --------------------------------------------------------------------------- #
_META_CONCURRENCY = 6          # metabuscas simultâneas (cada uma já abre 2 motores)
_ATTEMPTS = 3
_SEARCH_DEADLINE = 75.0        # segundos, fila inclusa (watchdog das tools = 120s)
_CACHE_TTL = 600.0
_CACHE_MAX = 500
_COOLDOWN_BLOCK = 300.0        # CAPTCHA / 429 / 403
_COOLDOWN_TIMEOUT = 60.0
_RETRY_BASE = 0.7              # espera entre tentativas: base × nº da tentativa + jitter

import contextvars

_ATTEMPT: contextvars.ContextVar[int] = contextvars.ContextVar("websearch_attempt", default=0)

_POOL = ThreadPoolExecutor(max_workers=_META_CONCURRENCY, thread_name_prefix="websearch")
_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, list[SearchResult]]] = {}
_INFLIGHT: dict[tuple, Future] = {}
_COOLING: dict[str, float] = {}   # motor da metabusca → até quando descansa


def _classify(err: str) -> float:
    e = err.lower()
    if any(k in e for k in ("ratelimit", "rate limit", "429", "403", "captcha", "blocked", "forbidden", "too many")):
        return _COOLDOWN_BLOCK
    if "timed out" in e or "timeout" in e or "dns" in e:
        return _COOLDOWN_TIMEOUT
    return 0.0


def _cool(engine: str, err: str) -> None:
    secs = _classify(err)
    if engine and secs:
        with _LOCK:
            _COOLING[engine] = max(_COOLING.get(engine, 0.0), time.monotonic() + secs)


class _EngineErrors(logging.Handler):
    """A ddgs só registra no log qual motor falhou ("Error in engine %s: %r"); é daqui
    que vem o descanso por motor. Repassa ao log normal só o que é WARNING+."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.msg == "Error in engine %s: %r" and record.args and len(record.args) >= 2:
                _cool(str(record.args[0]), repr(record.args[1]))
            elif record.levelno >= logging.WARNING:
                logging.getLogger("aiworkspace.search.ddgs").handle(record)
        except Exception:  # noqa: BLE001 - log nunca derruba a busca
            pass


def _install_engine_watch() -> None:
    lg = logging.getLogger("ddgs.ddgs")
    if not any(isinstance(h, _EngineErrors) for h in lg.handlers):
        lg.addHandler(_EngineErrors())
        lg.setLevel(logging.INFO)
        lg.propagate = False


def _all_engines() -> list[str]:
    try:
        from ddgs.engines import ENGINES
        return list(ENGINES["text"].keys())
    except Exception:  # noqa: BLE001
        return []


def _healthy_backend(engines: str, region: str = "wt-wt") -> str:
    """"auto" menos os motores em descanso. Se todos estão descansando, tenta todos.
    Sem região ("wt-wt") a Wikipedia da ddgs consulta wt.wikipedia.org, que não
    existe — falhava em TODA busca; fica de fora."""
    auto = (engines or "auto") in ("auto", "all")
    if not auto:
        pedidos = [e.strip() for e in engines.split(",") if e.strip()]
    else:
        pedidos = _all_engines()
        if not pedidos:
            return "auto"
        if (region or "wt-wt") == "wt-wt":
            pedidos = [e for e in pedidos if e != "wikipedia"]
    agora = time.monotonic()
    with _LOCK:
        livres = [e for e in pedidos if _COOLING.get(e, 0.0) <= agora]
    if not livres:
        return engines or "auto"
    if auto and len(livres) == len(pedidos) and len(pedidos) == len(_all_engines()):
        return "auto"
    return ",".join(livres)


# palavras que não dizem do que a busca trata (e pedaços de URL que todo resultado tem)
_STOP = frozenset("""
the and for with from what how why when where which who this that are was were you your
not but can does into about than then them they there their have has had will would
www com org net html http https htm php
que com para por uma uns umas dos das nos nas pelo pela pelos pelas como mais muito
sobre entre isso esta este essa esse onde qual quais quem quando porque seu sua seus
""".split())


def _terms(text: str) -> set[str]:
    """Palavras significativas (sem acento, minúsculas, 3+ letras, sem stopwords/números)."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in _STOP and not w.isdigit()}


def _relevant(query: str, found: list[SearchResult]) -> list[SearchResult]:
    """Só os resultados que mencionam ao menos uma palavra significativa da busca.
    Busca sem termos latinos (ex.: só ideogramas) passa direto."""
    qt = _terms(query)
    if not qt:
        return found
    out = []
    for r in found:
        doc = _terms(f"{r.get('title', '')} {r.get('content', '')} {r.get('url', '')}")
        if doc & qt:
            out.append(r)
    return out


def _simplify(query: str) -> str:
    """Busca sem aspas e sem operadores `chave=valor`/`site:` — é o formato que faz
    os motores errarem (o Yahoo devolve qualquer coisa). Mantém o valor dos
    operadores: `app="Cisco"` → `Cisco`."""
    q = re.sub(r'\b[\w.-]+[=:]\s*"([^"]*)"', r" \1 ", query)   # chave="valor" → valor
    q = re.sub(r"\b[\w.-]+[=:](\S*)", r" \1 ", q)              # chave=valor → valor; chave= some
    q = q.replace('"', " ")
    return " ".join(q.split())


def _cache_key(provider: str, query: str, cfg: SearchConfig) -> tuple:
    return (provider, " ".join(query.lower().split()), cfg.max_results, cfg.engines, cfg.region,
            bool(cfg.tavily_api_key), bool(cfg.brave_api_key))


async def _single_flight(key: tuple, make) -> list[SearchResult]:
    """Cache curto + UMA ida aos motores para buscas iguais simultâneas (vale entre
    threads/loops: quem chega depois espera o Future de quem já está buscando)."""
    agora = time.monotonic()
    from .. import tracing

    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > agora:
            tracing.annotate(cache="hit")
            return list(hit[1])
        fut = _INFLIGHT.get(key)
        dono = fut is None
        if dono:
            fut = Future()
            _INFLIGHT[key] = fut
    # "shared" = pegou carona na mesma busca de outro agente (voo único)
    tracing.annotate(cache="miss" if dono else "shared")
    if not dono:
        return list(await asyncio.wrap_future(fut))
    try:
        res = await make()
    except BaseException as exc:
        with _LOCK:
            _INFLIGHT.pop(key, None)
        fut.set_exception(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
        fut.exception()  # marca como observada (sem "Future exception was never retrieved")
        raise
    with _LOCK:
        _INFLIGHT.pop(key, None)
        if res:
            if len(_CACHE) >= _CACHE_MAX:
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_MAX // 5]:
                    _CACHE.pop(k, None)
            _CACHE[key] = (time.monotonic() + _CACHE_TTL, list(res))
    fut.set_result(res)
    return res


async def _with_retries(provider: str, query: str, cfg: SearchConfig) -> list[SearchResult]:
    fn = _PROVIDERS.get(provider, _PROVIDERS["metasearch"])
    tentativas = (_ATTEMPTS if provider in ("metasearch", "searxng", "duckduckgo")
                  else 1 if provider == "browser" else 2)
    ultimo: Exception | None = None
    from .. import tracing

    for i in range(tentativas):
        _ATTEMPT.set(i)
        tracing.annotate(attempts=i + 1)
        try:
            return await fn(query, cfg)
        except _NoKey:
            raise
        except Exception as exc:  # noqa: BLE001
            ultimo = exc
            if i + 1 < tentativas:
                await asyncio.sleep(_RETRY_BASE * (i + 1) + random.uniform(0, _RETRY_BASE))
    assert ultimo is not None
    raise ultimo


def _fallbacks(provider: str, cfg: SearchConfig) -> list[str]:
    if provider in ("tavily", "brave"):
        out = ["metasearch"]
    else:
        out = []
        if cfg.tavily_api_key:
            out.append("tavily")
        if cfg.brave_api_key:
            out.append("brave")
    if cfg.browser_search is not None:
        out.append("browser")
    return out


async def _resilient(provider: str, query: str, cfg: SearchConfig) -> list[SearchResult]:
    from .. import tracing

    erros: list[str] = []
    simples = _simplify(query)
    # 1º a busca como veio; sem nada RELEVANTE, a versão simplificada
    for q in dict.fromkeys([query, simples] if simples else [query]):
        for prov in [provider, *_fallbacks(provider, cfg)]:
            try:
                # um span por motor/provedor tentado (com as novas tentativas dentro)
                with tracing.span(f"search:{prov}", kind="http", provider=prov,
                                  fallback=prov != provider, simplified=q != query) as _sp:
                    try:
                        brutos = await _with_retries(prov, q, cfg)
                    except Exception as exc:  # noqa: BLE001
                        _sp.status = "error"
                        _sp.error = (str(exc).strip() or type(exc).__name__)[:300]
                        raise
                    found = _relevant(query, brutos)
                    _sp.set(results=len(found), dropped=len(brutos) - len(found))
            except Exception as exc:  # noqa: BLE001
                erros.append(f"{prov}: {(str(exc).strip() or type(exc).__name__)[:200]}")
                continue
            if found:
                return found
            if brutos:
                logger.info("busca: %s devolveu %d resultado(s) sem relação com %r — descartados",
                            prov, len(brutos), q[:120])
                erros.append(f"{prov}: resultados sem relação com a busca")
            else:
                erros.append(f"{prov}: sem resultados")
    # nada encontrado e nenhum outro provider para tentar: não é falha (era assim antes)
    if not _fallbacks(provider, cfg) and erros and all(e.endswith(": sem resultados") for e in erros):
        return []
    raise SearchProviderError("; ".join(erros) or "sem resultados")


async def _one(
    provider: str, query: str, cfg: SearchConfig
) -> tuple[list[SearchResult], str | None]:
    provider = (provider or "metasearch").lower()
    if provider not in _PROVIDERS:
        provider = "metasearch"
    from .. import tracing

    try:
        with tracing.span("search:query", kind="internal", provider=provider,
                          query_chars=len(query)) as _qs:
            found = await asyncio.wait_for(
                _single_flight(_cache_key(provider, query, cfg), lambda: _resilient(provider, query, cfg)),
                timeout=_SEARCH_DEADLINE,
            )
            _qs.set(results=len(found))
        return found, None
    except asyncio.TimeoutError:
        return [], f"{provider}: a busca demorou demais (muitas buscas na fila); tente de novo"
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
    """Metabusca da ddgs: dispara vários motores em paralelo e agrega/ordena. Roda no
    pool próprio (fila limitada) e pula os motores que estão descansando de um bloqueio."""
    _install_engine_watch()
    backend = _healthy_backend(engines or cfg.engines or "auto", cfg.region or "wt-wt")
    # nova tentativa: sorteia outros motores (a ddgs sempre começa pelos de maior
    # prioridade — sem isto a 2ª tentativa batia nos mesmos que acabaram de falhar)
    if _ATTEMPT.get() > 0:
        pool = backend.split(",") if backend != "auto" else [
            e for e in _all_engines() if (cfg.region or "wt-wt") != "wt-wt" or e != "wikipedia"]
        if len(pool) > 3:
            backend = ",".join(random.sample(pool, 3))

    from .. import tracing

    marcas: dict[str, float] = {"submit": time.monotonic()}

    def _run() -> list[SearchResult]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        marcas["start"] = time.monotonic()

        try:
            found = DDGS(timeout=6).text(
                query,
                region=cfg.region or "wt-wt",
                safesearch="moderate",
                max_results=cfg.max_results,
                backend=backend,
            )
        except DDGSException as exc:
            # "No results found." também é o que sai quando os motores devolvem página de
            # bloqueio sem erro — por isso conta como falha (e a próxima tentativa troca
            # de motores). Todos os motores falhando (ex.: CAPTCHA de IP de servidor): o motivo
            # precisa chegar ao teste de conexão e à tool
            raise SearchProviderError(f"metabusca sem resultado: {exc}") from exc
        return [
            {"title": r.get("title", ""), "url": r.get("href", r.get("url", "")), "content": r.get("body", "")}
            for r in found or []
        ]

    try:
        return await asyncio.wrap_future(_POOL.submit(_run))
    finally:
        # fila do pool (buscas demais ao mesmo tempo) vs tempo nos motores
        fim = time.monotonic()
        ini = marcas.get("start", fim)
        tracing.annotate(backend=backend, pool_queue_ms=round((ini - marcas["submit"]) * 1000, 1),
                         engines_ms=round((fim - ini) * 1000, 1))


class _NoKey(SearchProviderError):
    """Provider escolhido sem chave configurada: não adianta tentar de novo."""


# renderizar pesa: no máximo 2 buscas pelo navegador ao mesmo tempo
_BROWSER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="websearch-browser")


async def _browser(query: str, cfg: SearchConfig) -> list[SearchResult]:
    if cfg.browser_search is None:
        raise _NoKey("sem navegador configurado")
    found = await asyncio.wrap_future(_BROWSER_POOL.submit(cfg.browser_search, query, cfg.max_results))
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in found or []]


async def _tavily(query: str, cfg: SearchConfig) -> list[SearchResult]:
    if not cfg.tavily_api_key:
        raise _NoKey("configure a chave do Tavily")
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
        raise _NoKey("configure a chave do Brave")
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
