"""Recuperação por similaridade sobre a Base de Conhecimento.

Embed da consulta (FastEmbed, threadpool) + busca de vizinhos por cosseno no
pgvector (`embedding <=> :q`), filtrando pelas bases acopladas e pelo usuário.
Usa uma sessão própria (`SessionLocal`, engine da app — NÃO a sessão da request),
então funciona igual na geração em background / stream retomável.
"""

from __future__ import annotations

import logging
import uuid

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text as sql_text

from ..db import SessionLocal
from . import embeddings

logger = logging.getLogger(__name__)

_SEARCH = sql_text(
    "SELECT c.id AS chunk_id, c.doc_id, c.ordinal, c.text, d.filename, "
    "       c.embedding <=> CAST(:emb AS vector) AS dist "
    "FROM knowledge_chunks c "
    "JOIN knowledge_docs d ON d.id = c.doc_id "
    "WHERE c.user_id = :uid AND c.base_id = ANY(:bids) "
    "ORDER BY c.embedding <=> CAST(:emb AS vector) "
    "LIMIT :k"
)


def _uuids(ids: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for i in ids or []:
        try:
            out.append(i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)))
        except (ValueError, TypeError):
            continue
    return out


async def search(
    user_id, base_ids: list[str], query: str, k: int = 6
) -> list[dict]:
    """Top-`k` trechos mais relevantes das `base_ids` para `query`.

    Retorna [{chunk_id, doc_id, filename, ordinal, text, score}] (score 0..1,
    maior = mais parecido). Lista vazia se não houver base/consulta/match ou em erro.
    """
    bids = _uuids(base_ids)
    q = (query or "").strip()
    if not bids or not q:
        return []
    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return []
    try:
        vec = await run_in_threadpool(embeddings.embed_query, q)
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge embed_query falhou: %s", exc)
        return []
    lit = embeddings.to_pgvector(vec)
    try:
        async with SessionLocal() as db:
            res = await db.execute(
                _SEARCH, {"emb": lit, "uid": uid, "bids": bids, "k": max(1, int(k))}
            )
            rows = res.mappings().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge search falhou: %s", exc)
        return []
    out: list[dict] = []
    for r in rows:
        dist = float(r["dist"]) if r["dist"] is not None else 1.0
        out.append(
            {
                "chunk_id": str(r["chunk_id"]),
                "doc_id": str(r["doc_id"]),
                "filename": r["filename"] or "documento",
                "ordinal": int(r["ordinal"]),
                "text": r["text"] or "",
                "score": round(max(0.0, 1.0 - dist), 4),
            }
        )
    return out
