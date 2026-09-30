"""Troca do modelo de embedding: os vetores antigos são refeitos em segundo plano
(knowledge/reembed.py), guiados pela etiqueta do modelo em cada linha."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .conftest import migrar, pytestmark  # noqa: F401


def test_reembed_refaz_so_o_que_veio_de_outro_modelo(banco, engine, monkeypatch):
    from aiworkspace import db as dbmod
    from aiworkspace.knowledge import embeddings, reembed

    migrar(engine, "head")
    uid, bid, did = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    velho, novo, ja = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    zero = "[" + ",".join(["0"] * 384) + "]"
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, email, hashed_password, role, is_active, status, profile, token_version, totp_enabled, created_at, updated_at) "
                       "VALUES (:u, 'a@b.c', 'x', 'admin', true, 'active', '{}', 0, false, now(), now())"), {"u": uid})
        c.execute(text("INSERT INTO knowledge_bases (id, user_id, name, description, tags, kind, created_at, updated_at) "
                       "VALUES (:b, :u, 'kb', '', '[]', 'kb', now(), now())"), {"b": bid, "u": uid})
        c.execute(text("INSERT INTO knowledge_docs (id, base_id, user_id, filename, mime, size, status, chunk_count, created_at, updated_at) "
                       "VALUES (:d, :b, :u, 'a.txt', 'text/plain', 1, 'ready', 2, now(), now())"), {"d": did, "b": bid, "u": uid})
        for cid, tag in ((velho, ""), (novo, embeddings.MODEL)):
            c.execute(text("INSERT INTO knowledge_chunks (id, doc_id, base_id, user_id, ordinal, text, embedding, embed_model, created_at, updated_at) "
                           "VALUES (:i, :d, :b, :u, 0, 'contrato vence em março', CAST(:e AS vector), :t, now(), now())"),
                      {"i": cid, "d": did, "b": bid, "u": uid, "e": zero, "t": tag})
        c.execute(text("CREATE TABLE IF NOT EXISTS aiworkspace_memories (id uuid PRIMARY KEY, vector vector(384), payload jsonb)"))
        c.execute(text("INSERT INTO aiworkspace_memories VALUES (:i, CAST(:e AS vector), CAST(:p AS jsonb))"),
                  {"i": ja, "e": zero, "p": '{"data": "gosta de café", "user_id": "u"}'})

    um = "[" + ",".join(["1"] + ["0"] * 383) + "]"
    monkeypatch.setattr(embeddings, "embed_texts", lambda ts: [[1.0] + [0.0] * 383 for _ in ts])
    monkeypatch.setattr(embeddings, "embed_query", lambda t: [1.0] + [0.0] * 383)
    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    monkeypatch.setattr(dbmod, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))

    assert asyncio.run(reembed.run_once()) == 2          # 1 trecho antigo + 1 memória
    assert asyncio.run(reembed.run_once()) == 0          # nada mais a refazer
    with engine.begin() as c:
        rows = dict(c.execute(text("SELECT id, embedding::text FROM knowledge_chunks")).all())
        mem = c.execute(text("SELECT vector::text, payload->>'embed_model' FROM aiworkspace_memories")).one()
    assert rows[velho].startswith("[1") and rows[novo].startswith("[0")  # o atual não é mexido
    assert mem[0].startswith("[1") and mem[1] == embeddings.MODEL
    asyncio.run(eng.dispose())
