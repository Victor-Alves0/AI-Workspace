"""Embeddings locais para RAG.

Reusa o MESMO modelo/cache do mem0 (FastEmbed `BAAI/bge-small-en-v1.5`, 384 dims,
ONNX, sem chave de API) — ver `memory/mem0_service._fastembed_embedder`. Assim o
volume de cache (`FASTEMBED_CACHE_PATH`) é compartilhado e não baixamos o modelo
duas vezes. Tudo é bloqueante (CPU) → chamar via `run_in_threadpool`.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

DIMS = 384


@lru_cache(maxsize=1)
def _embedder():
    """FastEmbed (via LangChain) cacheado. Mesma instância do mem0."""
    from ..memory.mem0_service import _fastembed_embedder

    return _fastembed_embedder()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeddings de vários textos (documentos). Bloqueante."""
    if not texts:
        return []
    return _embedder().embed_documents(texts)


def embed_query(text: str) -> list[float]:
    """Embedding de uma consulta. Bloqueante."""
    return _embedder().embed_query(text or "")


def to_pgvector(vec: list[float]) -> str:
    """Serializa um vetor para o literal aceito pelo pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
