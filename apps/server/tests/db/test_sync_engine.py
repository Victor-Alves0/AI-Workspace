"""Sincronização entre instâncias contra o Postgres real: dois bancos fazem o papel de
duas instalações (servidor e desktop), CADA UMA COM A PRÓPRIA CHAVE de cifra, com a
mesma conta (mesmo e-mail, ids diferentes) e uma conta que só existe num lado."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from .conftest import criar_banco, dropar_banco, migrar, pytestmark, sync_url  # noqa: F401


def _async(url: str):
    return create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def mundo():
    """A = servidor (chave do processo), B = desktop (outra chave)."""
    from aiworkspace import crypto
    from aiworkspace.models import Chat, Message, ModelConfig, User, UserSecret
    from aiworkspace.secret_rotation import rotate_keys

    urls = [criar_banco(), criar_banco()]
    try:
        engs = [create_engine(sync_url(u)) for u in urls]
        for e in engs:
            migrar(e, "head")
        ida, idb, id_outro = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        velho = datetime(2020, 1, 1, tzinfo=UTC)
        with Session(engs[0]) as s:
            s.add_all([User(id=ida, email="dono@casa.local", hashed_password="x"),
                       User(id=id_outro, email="outro@casa.local", hashed_password="x")])
            s.flush()
            mc_a = ModelConfig(user_id=ida, base_model="x/y", name="GPT de A", slug="gpt")
            s.add(mc_a)
            s.flush()
            chat = Chat(user_id=ida, title="Plano", model="x/y", system_prompt="segredo do chat",
                        model_config_id=mc_a.id)
            s.add_all([chat, Chat(user_id=id_outro, title="Só do outro", model="x/y")])
            s.flush()
            s.add(Message(chat_id=chat.id, role="user", content="olá do servidor"))
            s.add(UserSecret(user_id=ida, name="openrouter", ciphertext=crypto.encrypt("sk-A")))
            s.commit()
            chat_a, mc_a_id = chat.id, mc_a.id
        with Session(engs[1]) as s:
            s.add(User(id=idb, email="Dono@Casa.local", hashed_password="x"))
            s.flush()
            mc_b = ModelConfig(user_id=idb, base_model="x/y", name="GPT de B", slug="gpt",
                               updated_at=velho, created_at=velho)
            s.add(mc_b)
            s.add(UserSecret(user_id=idb, name="openrouter", ciphertext=crypto.encrypt("sk-B"),
                             updated_at=velho, created_at=velho))
            s.flush()
            chat_b = Chat(user_id=idb, title="Rascunho do desktop", model="x/y", model_config_id=mc_b.id)
            s.add(chat_b)
            s.commit()
            chat_b_id, mc_b_id = chat_b.id, mc_b.id
        fb = Fernet(Fernet.generate_key())
        rotate_keys(crypto._fernet(), fb, dry_run=False, database_url=urls[1])
        yield {
            "urls": urls, "engs": engs, "fa": crypto._fernet(), "fb": fb,
            "ida": str(ida), "idb": str(idb), "chat_a": str(chat_a), "chat_b": str(chat_b_id),
            "mc_a": str(mc_a_id), "mc_b": str(mc_b_id),
        }
    finally:
        for e in locals().get("engs", []):
            e.dispose()
        for u in urls:
            dropar_banco(u)


async def _enable(url):
    from aiworkspace.sync import capture

    eng = _async(url)
    async with eng.begin() as c:
        await capture.enable(c)
        inst = await capture.instance(c)
    await eng.dispose()
    return inst["id"]


async def _sync(src_url, dst_url, *, dst_id, src_users, f_src, f_dst, user_map, since=0):
    """Uma ida: coleta em `src`, aplica em `dst`."""
    from aiworkspace.sync import engine

    es, ed = _async(src_url), _async(dst_url)
    try:
        async with es.begin() as c:
            lote = await engine.collect(c, since=since, exclude_origin=dst_id, local_users=src_users, fernet=f_src)
        async with ed.begin() as c:
            res = await engine.apply(c, lote["changes"], fernet=f_dst, user_map=user_map)
    finally:
        await es.dispose()
        await ed.dispose()
    return lote, res


def _q(eng, sql, **kw):
    with eng.connect() as c:
        return c.execute(text(sql), kw).all()


def test_ida_e_volta_com_chaves_diferentes(mundo):
    m = mundo
    a, b = m["urls"]
    inst_a, inst_b = run(_enable(a)), run(_enable(b))

    # servidor → desktop
    lote, res = run(_sync(a, b, dst_id=inst_b, src_users={m["ida"]}, f_src=m["fa"], f_dst=m["fb"],
                          user_map={m["ida"]: m["idb"]}))
    assert res["errors"] == 0 and res["conflicts"] == 0 and not res["pending"]
    eb = m["engs"][1]
    chat = _q(eb, "SELECT user_id, title, system_prompt FROM chats WHERE id = :i", i=m["chat_a"])[0]
    assert str(chat.user_id) == m["idb"] and chat.title == "Plano"
    # o prompt cifrado chegou legível com a chave do DESKTOP
    assert m["fb"].decrypt(chat.system_prompt.removeprefix("enc:v1:").encode()).decode() == "segredo do chat"
    msg = _q(eb, "SELECT content FROM messages WHERE chat_id = :i", i=m["chat_a"])[0]
    assert m["fb"].decrypt(msg.content.removeprefix("enc:v1:").encode()).decode() == "olá do servidor"
    # conta que só existe no servidor não desce
    assert not _q(eb, "SELECT 1 FROM chats WHERE title = 'Só do outro'")

    # "a mesma coisa" com ids diferentes vira UMA linha só, com o id comum:
    segredos = _q(eb, "SELECT id, ciphertext FROM user_secrets WHERE name = 'openrouter'")
    assert len(segredos) == 1
    assert m["fb"].decrypt(segredos[0].ciphertext.encode()).decode() == "sk-A"   # a do servidor era a mais nova
    modelos = _q(eb, "SELECT id, name FROM model_configs WHERE slug = 'gpt'")
    assert [(str(r.id), r.name) for r in modelos] == [(m["mc_a"], "GPT de A")]
    # e o chat do desktop que usava o modelo dele segue apontando para ele (id novo)
    assert str(_q(eb, "SELECT model_config_id FROM chats WHERE id = :i", i=m["chat_b"])[0][0]) == m["mc_a"]

    # desktop → servidor: não devolve o que veio do servidor (sem eco)
    lote, res = run(_sync(b, a, dst_id=inst_a, src_users={m["idb"]}, f_src=m["fb"], f_dst=m["fa"],
                          user_map={m["idb"]: m["ida"]}))
    assert all(ch["o"] != inst_a for ch in lote["changes"])
    ea = m["engs"][0]
    rasc = _q(ea, "SELECT user_id, title FROM chats WHERE id = :i", i=m["chat_b"])[0]
    assert str(rasc.user_id) == m["ida"] and rasc.title == "Rascunho do desktop"


def test_mais_recente_vence_e_exclusao_propaga(mundo):
    m = mundo
    a, b = m["urls"]
    inst_a, inst_b = run(_enable(a)), run(_enable(b))
    ida_b = {m["ida"]: m["idb"]}
    run(_sync(a, b, dst_id=inst_b, src_users={m["ida"]}, f_src=m["fa"], f_dst=m["fb"], user_map=ida_b))

    ea, eb = m["engs"]
    with ea.begin() as c:
        c.execute(text("UPDATE chats SET title = 'editado no servidor' WHERE id = :i"), {"i": m["chat_a"]})
    with eb.begin() as c:   # depois: é o mais recente
        c.execute(text("UPDATE chats SET title = 'editado no desktop' WHERE id = :i"), {"i": m["chat_a"]})
    pos_a = _q(ea, "SELECT max(seq) FROM sync_rows")[0][0]
    run(_sync(a, b, dst_id=inst_b, src_users={m["ida"]}, f_src=m["fa"], f_dst=m["fb"], user_map=ida_b))
    run(_sync(b, a, dst_id=inst_a, src_users={m["idb"]}, f_src=m["fb"], f_dst=m["fa"],
              user_map={m["idb"]: m["ida"]}))
    for e in (ea, eb):
        assert _q(e, "SELECT title FROM chats WHERE id = :i", i=m["chat_a"])[0][0] == "editado no desktop"

    # apagar no servidor apaga no desktop (e as mensagens vão junto)
    with ea.begin() as c:
        c.execute(text("DELETE FROM chats WHERE id = :i"), {"i": m["chat_a"]})
    run(_sync(a, b, dst_id=inst_b, src_users={m["ida"]}, f_src=m["fa"], f_dst=m["fb"], user_map=ida_b,
              since=pos_a))
    assert not _q(eb, "SELECT 1 FROM chats WHERE id = :i", i=m["chat_a"])
    assert not _q(eb, "SELECT 1 FROM messages WHERE chat_id = :i", i=m["chat_a"])


def test_filho_antes_do_pai_espera_e_depois_entra(mundo):
    from aiworkspace.sync import engine

    m = mundo
    a, b = m["urls"]
    inst_a, inst_b = run(_enable(a)), run(_enable(b))

    async def _go():
        ea = _async(a)
        async with ea.begin() as c:
            lote = await engine.collect(c, since=0, exclude_origin=inst_b, local_users={m["ida"]}, fernet=m["fa"])
        await ea.dispose()
        so_msg = [ch for ch in lote["changes"] if ch["t"] == "messages"]
        resto = [ch for ch in lote["changes"] if ch["t"] != "messages"]
        eb = _async(b)
        async with eb.begin() as c:
            r1 = await engine.apply(c, so_msg, fernet=m["fb"], user_map={m["ida"]: m["idb"]})
        async with eb.begin() as c:
            r2 = await engine.apply(c, resto + r1["pending"], fernet=m["fb"], user_map={m["ida"]: m["idb"]})
        await eb.dispose()
        return r1, r2

    r1, r2 = run(_go())
    assert len(r1["pending"]) == 1 and r1["applied"] == 0
    assert not r2["pending"] and r2["errors"] == 0
    assert _q(m["engs"][1], "SELECT 1 FROM messages WHERE chat_id = :i", i=m["chat_a"])


def test_memorias_trocam_o_dono_pelo_email(mundo):
    import json

    m = mundo
    a, b = m["urls"]
    with m["engs"][0].begin() as c:
        c.execute(text("INSERT INTO aiworkspace_memories (id, payload) VALUES (:i, CAST(:p AS jsonb))"),
                  {"i": str(uuid.uuid4()), "p": json.dumps({"user_id": m["ida"], "data": "gosta de café"})})
    inst_a, inst_b = run(_enable(a)), run(_enable(b))
    run(_sync(a, b, dst_id=inst_b, src_users={m["ida"]}, f_src=m["fa"], f_dst=m["fb"],
              user_map={m["ida"]: m["idb"]}))
    rows = _q(m["engs"][1], "SELECT payload FROM aiworkspace_memories")
    assert [(r[0]["user_id"], r[0]["data"]) for r in rows] == [(m["idb"], "gosta de café")]
