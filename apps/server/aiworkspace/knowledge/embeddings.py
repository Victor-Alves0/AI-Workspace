"""Embeddings locais (FastEmbed `BAAI/bge-small-en-v1.5`, 384 dims, ONNX, sem chave).

Uma instância só, usada pela Base de Conhecimento e pela memória do usuário
(`memory/memory_service`), que guardam vetores no pgvector. Tudo é bloqueante (CPU)
→ chamar via `run_in_threadpool`.

Compatibilidade com os vetores já gravados: antes o modelo passava pelo LangChain
(`FastEmbedEmbeddings`), que cria o `TextEmbedding` com max_length=512 e usa `embed`
para documentos e `query_embed` para consultas. Aqui é a MESMA chamada, direto no
fastembed — vetores idênticos, nada a recalcular.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

MODEL = "BAAI/bge-small-en-v1.5"
DIMS = 384
_BATCH = 256


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=MODEL, max_length=512)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeddings de vários textos (documentos). Bloqueante."""
    if not texts:
        return []
    return [v.tolist() for v in _model().embed(texts, batch_size=_BATCH)]


def embed_query(text: str) -> list[float]:
    """Embedding de uma consulta. Bloqueante."""
    return next(iter(_model().query_embed(text or "", batch_size=_BATCH))).tolist()


def to_pgvector(vec: list[float]) -> str:
    """Serializa um vetor para o literal aceito pelo pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
