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
    "SELECT c.id AS chunk_id, c.doc_id, c.ordinal, c.text, d.filename, d.mime, "
    "       c.embedding <=> CAST(:emb AS vector) AS dist "
    "FROM knowledge_chunks c "
    "JOIN knowledge_docs d ON d.id = c.doc_id "
    "WHERE c.user_id = :uid AND c.base_id = ANY(:bids) "
    "ORDER BY c.embedding <=> CAST(:emb AS vector) "
    "LIMIT :k"
)

# variante com filtro por documento (usada pela referência "#" a arquivos grandes)
_SEARCH_DOCS = sql_text(
    "SELECT c.id AS chunk_id, c.doc_id, c.ordinal, c.text, d.filename, d.mime, "
    "       c.embedding <=> CAST(:emb AS vector) AS dist "
    "FROM knowledge_chunks c "
    "JOIN knowledge_docs d ON d.id = c.doc_id "
    "WHERE c.user_id = :uid AND c.base_id = ANY(:bids) AND c.doc_id = ANY(:dids) "
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


async def _embed_query(query: str):
    """Embed da consulta → literal pgvector, ou None (consulta vazia / erro)."""
    q = (query or "").strip()
    if not q:
        return None
    try:
        vec = await run_in_threadpool(embeddings.embed_query, q)
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge embed_query falhou: %s", exc)
        return None
    return embeddings.to_pgvector(vec)


def _row_to_hit(r) -> dict:
    dist = float(r["dist"]) if r["dist"] is not None else 1.0
    return {
        "chunk_id": str(r["chunk_id"]),
        "doc_id": str(r["doc_id"]),
        "filename": r["filename"] or "documento",
        "mime": r["mime"] or "",
        "ordinal": int(r["ordinal"]),
        "text": r["text"] or "",
        "score": round(max(0.0, 1.0 - dist), 4),
    }


async def search(
    user_id, base_ids: list[str], query: str, k: int = 6,
    doc_ids: list[str] | None = None,
) -> list[dict]:
    """Top-`k` trechos mais relevantes das `base_ids` para `query`.

    Com `doc_ids`, restringe a busca a esses documentos (usado pela referência "#"
    a arquivos grandes). Retorna [{chunk_id, doc_id, filename, ordinal, text, score}]
    (score 0..1, maior = mais parecido). Lista vazia se não houver base/consulta/match
    ou em erro.
    """
    bids = _uuids(base_ids)
    q = (query or "").strip()
    if not bids or not q:
        return []
    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return []
    lit = await _embed_query(q)
    if lit is None:
        return []
    dids = _uuids(doc_ids or [])
    try:
        async with SessionLocal() as db:
            if dids:
                res = await db.execute(
                    _SEARCH_DOCS,
                    {"emb": lit, "uid": uid, "bids": bids, "dids": dids, "k": max(1, int(k))},
                )
            else:
                res = await db.execute(
                    _SEARCH, {"emb": lit, "uid": uid, "bids": bids, "k": max(1, int(k))}
                )
            rows = res.mappings().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge search falhou: %s", exc)
        return []
    return [_row_to_hit(r) for r in rows]


async def search_multi(
    user_id, base_ids: list[str], query: str,
    ks: dict[str, int] | None = None, default_k: int = 6,
) -> list[dict]:
    """Como `search`, mas com k POR BASE (`ks`: {base_id: k}). Cada base traz o
    seu próprio número de trechos; o resultado é a UNIÃO, ordenada por score
    (maior = mais parecido). Embed da consulta uma única vez."""
    ks = ks or {}
    bids = _uuids(base_ids)
    q = (query or "").strip()
    if not bids or not q:
        return []
    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return []
    lit = await _embed_query(q)
    if lit is None:
        return []
    out: list[dict] = []
    try:
        async with SessionLocal() as db:
            for b in bids:
                k = max(1, int(ks.get(str(b), default_k) or default_k))
                res = await db.execute(_SEARCH, {"emb": lit, "uid": uid, "bids": [b], "k": k})
                out.extend(_row_to_hit(r) for r in res.mappings().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge search_multi falhou: %s", exc)
        return []
    out.sort(key=lambda h: h["score"], reverse=True)
    return out
