"""Pesquisa na web com providers plugáveis."""

from .providers import (
    SearchConfig,
    SearchProviderError,
    SearchResult,
    web_search,
    web_search_detailed,
)

__all__ = [
    "SearchConfig",
    "SearchProviderError",
    "SearchResult",
    "web_search",
    "web_search_detailed",
]
