"""Deep Search (pesquisa profunda) — agente de pesquisa iterativo.

Fluxo: planeja sub-perguntas → busca cada uma → (opcional) lê as páginas → resume
por rodada com citações → reflete se falta algo → itera → sintetiza um resumo final
com fontes.

Economia de tokens (princípio do SIFT): TODO o trabalho pesado (buscas, leitura de
páginas, resumos) roda AQUI, com um modelo configurável (idealmente barato). O
modelo principal do chat recebe só o resultado destilado (resumo + fontes) — as
páginas cruas nunca entram no contexto dele.

Usado só quando o usuário PEDE explicitamente uma pesquisa profunda; para um fato
rápido, o modelo deve usar `web.search.query`.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from .providers import openrouter

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; AIWorkspace-DeepSearch/1.0)"
_DEFAULT_MODEL = "openai/gpt-4o-mini"


@dataclass
class DeepSearchConfig:
    api_key: str | None = None            # chave OpenRouter (p/ os passos internos)
    model: str = ""                        # modelo dos passos internos (barato)
    max_subqueries: int = 3                # amplitude (sub-perguntas por rodada)
    max_rounds: int = 2                    # profundidade (iterações c/ reflexão)
    max_results_per_query: int = 4
    read_pages: bool = True                # ler páginas (mais completo) vs. só trechos
    max_pages: int = 4                     # páginas lidas por rodada
    max_page_chars: int = 4000             # corte por página (economia de tokens)
    web_search: Callable[[str], Awaitable[list[dict]]] | None = None


# ------------------------------ helpers de texto --------------------------- #
def _parse_json(raw: str) -> Any:
    m = re.search(r"\{.*\}|\[.*\]", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_list(raw: str) -> list[str]:
    data = _parse_json(raw)
    if isinstance(data, list):
        return [str(x).strip() for x in data if str(x).strip()]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
    # fallback: linhas
    return [ln.strip("-*0123456789. ").strip() for ln in (raw or "").splitlines() if ln.strip()][:5]


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    t = _TAG_RE.sub(" ", html or "")
    t = _HTML_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _WS_RE.sub(" ", t).strip()


def _is_public_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        host = p.hostname.lower()
        if host in ("localhost",) or host.endswith(".local"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            pass  # hostname (não IP) — segue
        return True
    except Exception:  # noqa: BLE001
        return False


async def _fetch_text(client: httpx.AsyncClient, url: str, max_chars: int) -> str:
    if not _is_public_url(url):
        return ""
    try:
        r = await client.get(url, headers={"User-Agent": _UA}, timeout=8, follow_redirects=True)
        ct = r.headers.get("content-type", "")
        if r.status_code != 200 or "html" not in ct and "text" not in ct:
            return ""
        return _html_to_text(r.text)[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


def _corpus(sources: list[dict]) -> str:
    out = []
    for i, s in enumerate(sources, 1):
        body = (s.get("content") or s.get("snippet") or "").strip()
        out.append(f"[{i}] {s.get('title','')} ({s.get('url','')})\n{body}")
    return "\n\n".join(out)


# --------------------------------- execução -------------------------------- #
async def run(query: str, cfg: DeepSearchConfig) -> dict:
    query = (query or "").strip()
    if not query:
        return {"error": "provide a research topic/question"}
    if not cfg.api_key:
        return {"error": "OpenRouter key not configured (needed for deep search)"}
    if not cfg.web_search:
        return {"error": "web search unavailable (configure a search engine)"}

    model = cfg.model or _DEFAULT_MODEL

    async def llm(system: str, user: str, max_tokens: int = 700) -> str:
        try:
            return await openrouter.complete(
                cfg.api_key, model,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                params={"max_tokens": max_tokens, "temperature": 0.2},
                timeout=90.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("deep_search llm falhou: %s", exc)
            return ""

    # 1) planejar sub-perguntas
    plan = await llm(
        "You plan web research. Output ONLY a JSON array of 2-4 short, distinct search queries that together cover the topic.",
        f"Topic: {query}", 200,
    )
    subqs = (_parse_list(plan) or [query])[: cfg.max_subqueries]

    sources: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient() as client:
        for rnd in range(max(1, cfg.max_rounds)):
            round_sources: list[dict] = []
            for q in subqs:
                try:
                    results = await cfg.web_search(q)
                except Exception:  # noqa: BLE001
                    results = []
                for r in (results or [])[: cfg.max_results_per_query]:
                    url = r.get("url")
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    round_sources.append({
                        "title": (r.get("title") or "")[:160],
                        "url": url,
                        "snippet": (r.get("content") or "").strip()[:400],
                    })
            if cfg.read_pages:
                for s in round_sources[: cfg.max_pages]:
                    txt = await _fetch_text(client, s["url"], cfg.max_page_chars)
                    if txt:
                        s["content"] = txt
            sources.extend(round_sources)

            if not round_sources:
                break

            note = await llm(
                "Extract the facts relevant to the topic from these sources. Cite as [n] using the source numbers. Be concise and factual; ignore irrelevant sources.",
                f"Topic: {query}\n\nSources:\n{_corpus(round_sources)}", 700,
            )
            if note:
                notes.append(note)

            # refletir: precisa de mais? (não na última rodada)
            if rnd < cfg.max_rounds - 1:
                refl = await llm(
                    'Given the notes so far, is the topic covered thoroughly? Output ONLY JSON: {"enough": true|false, "followups": ["query", ...]}.',
                    f"Topic: {query}\n\nNotes:\n" + "\n\n".join(notes), 200,
                )
                data = _parse_json(refl) or {}
                if data.get("enough"):
                    break
                fu = [str(x).strip() for x in (data.get("followups") or []) if str(x).strip()]
                if not fu:
                    break
                subqs = fu[: cfg.max_subqueries]

    if not sources:
        return {"error": "no sources found for this topic"}

    src_list = "\n".join(f"[{i}] {s['title']} — {s['url']}" for i, s in enumerate(sources, 1))
    brief = await llm(
        "Write a thorough but concise research brief that answers the topic, citing sources inline as [n]. Use short paragraphs; end with a '## Key findings' bullet list.",
        f"Topic: {query}\n\nNotes:\n" + "\n\n".join(notes) + f"\n\nSources:\n{src_list}", 1300,
    )
    if not brief:
        return {"error": "synthesis failed (check the deep-search model / OpenRouter key)"}

    return {
        "kind": "deep_research",
        "query": query,
        "brief": brief,
        "sources": [{"n": i, "title": s["title"], "url": s["url"]} for i, s in enumerate(sources, 1)],
        "rounds": len(notes),
    }
