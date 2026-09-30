"""Recalcula, em segundo plano, os vetores gerados por outro modelo de embedding.

Vetores de modelos diferentes não se comparam: depois de trocar o modelo
(`embeddings.MODEL`), cada trecho da Base de Conhecimento e cada memória antigos
precisam ser refeitos, senão a busca devolve lixo para eles. Cada linha guarda o
modelo que a gerou (`knowledge_chunks.embed_model`, `payload.embed_model` da
memória); este laço acha as diferentes e refaz em lotes pequenos. Roda no boot e de
hora em hora — cobre também as linhas antigas que chegarem pela sincronização.
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import text as sql_text
from starlette.concurrency import run_in_threadpool

from . import embeddings

logger = logging.getLogger(__name__)

_BATCH = 64
_INTERVAL = 3600


async def run_once() -> int:
    """Refaz tudo o que está com outro modelo. Devolve quantas linhas refez."""
    from ..db import SessionLocal

    feitos = 0
    modelo = embeddings.MODEL
    while True:  # trechos da Base de Conhecimento
        async with SessionLocal() as db:
            rows = (await db.execute(sql_text(
                "SELECT id, text FROM knowledge_chunks WHERE embed_model <> :m LIMIT :n"),
                {"m": modelo, "n": _BATCH})).all()
            if not rows:
                break
            vecs = await run_in_threadpool(embeddings.embed_texts, [r.text or "" for r in rows])
            for r, v in zip(rows, vecs):
                await db.execute(sql_text(
                    "UPDATE knowledge_chunks SET embedding = CAST(:e AS vector), embed_model = :m "
                    "WHERE id = :id"), {"e": embeddings.to_pgvector(v), "m": modelo, "id": r.id})
            await db.commit()
            feitos += len(rows)
    async with SessionLocal() as db:  # a tabela de memória nasce no 1º uso
        tem_memoria = (await db.execute(sql_text(
            "SELECT to_regclass('aiworkspace_memories') IS NOT NULL"))).scalar()
    while tem_memoria:  # memórias (a memória usa embed_query para gravar e buscar)
        async with SessionLocal() as db:
            rows = (await db.execute(sql_text(
                "SELECT id, payload->>'data' AS data FROM aiworkspace_memories "
                "WHERE coalesce(payload->>'embed_model', '') <> :m LIMIT :n"),
                {"m": modelo, "n": _BATCH})).all()
            if not rows:
                break
            for r in rows:
                v = await run_in_threadpool(embeddings.embed_query, r.data or "")
                await db.execute(sql_text(
                    "UPDATE aiworkspace_memories SET vector = CAST(:e AS vector), "
                    "payload = payload || CAST(:p AS jsonb) WHERE id = :id"),
                    {"e": embeddings.to_pgvector(v), "p": json.dumps({"embed_model": modelo}), "id": r.id})
            await db.commit()
            feitos += len(rows)
    if feitos:
        logger.info("embeddings: %d vetor(es) refeito(s) com %s", feitos, modelo)
    return feitos


async def loop() -> None:
    await asyncio.sleep(20)  # deixa o boot respirar
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - tenta de novo no próximo ciclo
            logger.warning("recálculo de embeddings falhou: %s", exc)
        await asyncio.sleep(_INTERVAL)
