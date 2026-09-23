"""Migração entre instalações (servidor → desktop): os segredos cifrados com a chave
de dados da ORIGEM são recifrados para a chave LOCAL — colunas EncryptedText, as
chaves de API (user_secrets) e os tokens guardados DENTRO de JSON (app_settings:
tokens do ChatGPT, client secrets OAuth), que a rotação antiga não alcançava."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from .conftest import migrar, pytestmark  # noqa: F401


def test_recifra_colunas_chaves_de_api_e_json(banco, engine):
    from aiworkspace.secret_rotation import _fernet_for, rotate_keys

    migrar(engine, "head")
    origem, local = _fernet_for("A" * 40), _fernet_for("B" * 40)
    uid, chat, art = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tok = lambda s: origem.encrypt(s.encode()).decode()  # noqa: E731
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, email, hashed_password) VALUES (:i, :e, 'x')"),
                  {"i": uid, "e": f"{uid.hex[:8]}@t.local"})
        c.execute(text("INSERT INTO user_secrets (id, user_id, name, ciphertext) VALUES (:i, :u, 'openrouter', :c)"),
                  {"i": uuid.uuid4(), "u": uid, "c": tok("sk-or-123")})
        c.execute(text("INSERT INTO app_settings (id, key, value) VALUES (:i, 'chatgpt:x', CAST(:v AS jsonb))"),
                  {"i": uuid.uuid4(), "v": json.dumps({"access_enc": tok("at-1"), "email": "a@b", "lista": [tok("rt-2")]})})
        c.execute(text("INSERT INTO chats (id, user_id, title) VALUES (:i, :u, 't')"), {"i": chat, "u": uid})
        c.execute(text("INSERT INTO artifacts (id, chat_id, user_id, identifier, content) "
                       "VALUES (:i, :c, :u, 'x', :t)"),
                  {"i": art, "c": chat, "u": uid, "t": "enc:v1:" + tok("conteúdo do artefato")})

    res = rotate_keys(origem, local, dry_run=False, database_url=banco)
    assert res["failed"] == 0 and res["changed"] >= 4

    with engine.connect() as c:
        ck = c.execute(text("SELECT ciphertext FROM user_secrets WHERE user_id = :u"), {"u": uid}).scalar()
        cfg = c.execute(text("SELECT value FROM app_settings WHERE key = 'chatgpt:x'")).scalar()
        cont = c.execute(text("SELECT content FROM artifacts WHERE id = :i"), {"i": art}).scalar()
    assert local.decrypt(ck.encode()) == b"sk-or-123"
    assert local.decrypt(cfg["access_enc"].encode()) == b"at-1"
    assert local.decrypt(cfg["lista"][0].encode()) == b"rt-2" and cfg["email"] == "a@b"
    assert local.decrypt(cont.removeprefix("enc:v1:").encode()).decode() == "conteúdo do artefato"

    # de novo: tudo já está na chave local → nada muda (retomável)
    again = rotate_keys(origem, local, dry_run=False, database_url=banco)
    assert again["changed"] == 0 and again["failed"] == 0
