"""Registro de watchers (monitores determinísticos).

Cada watcher implementa:

    async def check(config: dict, state: dict, deps: WatcherDeps) -> dict

e devolve:
    {
      "changed": bool,          # houve mudança/evento relevante?
      "summary_input": str,     # texto (já extraído) p/ o LLM resumir — só quando changed
      "new_state": dict,        # estado a persistir p/ a próxima comparação
      "error": str | None,      # erro (transitório) — o chamador reagenda com backoff
    }

Princípio: NUNCA mandar HTML/página crua ao LLM. O watcher extrai os campos e
compara com o estado anterior; o LLM (opcional) só entra para resumir a mudança.
Novos tipos entram só adicionando um módulo e registrando em WATCHERS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from . import page, price, rss, web_search


@dataclass
class WatcherDeps:
    # busca na web: async (query) -> list[{title, url, content}]
    web_search: Callable[[str], Awaitable[list[dict]]] | None = None
    # config de finanças (FinanceConfig) já com web_search injetado, p/ o PriceWatcher
    finance_cfg: Any = None
    # LLM barato p/ avaliar condição/resumir: async (system, user, max_tokens) -> str
    llm: Callable[..., Awaitable[str]] | None = None


WATCHERS: dict[str, Callable[[dict, dict, WatcherDeps], Awaitable[dict]]] = {
    "page": page.check,
    "web_search": web_search.check,
    "price": price.check,
    "rss": rss.check,
}

# rótulos p/ a UI / descoberta
WATCHER_TYPES = [
    {"key": "page", "name": "Mudança em página"},
    {"key": "web_search", "name": "Busca na web + condição"},
    {"key": "price", "name": "Preço de ativo"},
    {"key": "rss", "name": "RSS / notícias"},
]


async def check(watcher_type: str, config: dict, state: dict, deps: WatcherDeps) -> dict:
    fn = WATCHERS.get(watcher_type or "")
    if fn is None:
        return {"changed": False, "new_state": state or {}, "error": f"tipo de monitor desconhecido: {watcher_type}"}
    return await fn(config or {}, state or {}, deps)
