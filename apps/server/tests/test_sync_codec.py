"""Sincronização entre instâncias — as peças sem banco: conversão das linhas (segredos
recifrados com a chave de quem recebe, conta trocada pelo e-mail, links assinados
reassinados) e o envelope da troca (cifrado, autenticado, com validade)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from aiworkspace.sync import codec, engine, service
from aiworkspace.sync.tables import EXCLUDED, RAW_MEMORIES, synced


def test_o_que_fica_de_fora_da_sync():
    tabelas = synced()
    # rodariam em dobro / são da máquina
    for t in ("automations", "telegram_connections", "whatsapp_connections", "codespace_projects",
              "app_settings", "users", "sync_peers"):
        assert t in EXCLUDED and t not in tabelas
    # dado do usuário entra (inclusive as memórias, fora do ORM)
    for t in ("chats", "messages", "user_secrets", "model_configs", "knowledge_docs", RAW_MEMORIES):
        assert t in tabelas
    # pai antes do filho
    ordem = list(tabelas)
    assert ordem.index("chats") < ordem.index("messages") < len(ordem)
    # coluna que aponta para fora da sync não viaja
    assert "project_id" in tabelas["chats"].dropped_cols


def test_segredo_sai_da_chave_de_la_e_chega_na_de_ca():
    fa, fb = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    info = synced()["user_secrets"]
    linha = {"id": "s1", "user_id": "ua", "name": "openrouter", "ciphertext": fa.encrypt(b"sk-1").decode()}
    viagem = codec.outgoing(info, linha, fa)
    assert viagem["ciphertext"] == {"$f": "sk-1"}
    chegou = codec.incoming(info, viagem, fb, {"ua": "ub"})
    assert chegou["user_id"] == "ub" and fb.decrypt(chegou["ciphertext"].encode()) == b"sk-1"


def test_coluna_cifrada_e_json_com_token():
    fa, fb = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    info = synced()["chats"]
    linha = {"id": "c1", "user_id": "ua", "project_id": "p1",
             "system_prompt": "enc:v1:" + fa.encrypt(b"segredo").decode(),
             "params": {"token": fa.encrypt(b"tok").decode(), "n": 1}}
    viagem = codec.outgoing(info, linha, fa)
    assert "project_id" not in viagem
    chegou = codec.incoming(info, viagem, fb, {"ua": "ub"})
    assert fb.decrypt(chegou["system_prompt"].removeprefix("enc:v1:").encode()) == b"segredo"
    assert fb.decrypt(chegou["params"]["token"].encode()) == b"tok" and chegou["params"]["n"] == 1


def test_conta_que_nao_e_comum_nao_entra():
    info = synced()["chats"]
    f = Fernet(Fernet.generate_key())
    assert codec.incoming(info, {"id": "c1", "user_id": "desconhecido"}, f, {"ua": "ub"}) is None


def test_links_assinados_sao_reassinados_com_a_chave_local():
    from aiworkspace.uploads_service import sign_url

    uid = "0b1c2d3e-0000-4000-8000-000000000003"
    txt = f"veja [nota](http://outra:8000/uploads/{uid}?t=token.de.la) ok"
    novo = codec.resign(txt)
    assert f"(/uploads/{uid}?t=" in novo and "token.de.la" not in novo and "outra:8000" not in novo
    assert novo.split("(")[1].split(")")[0] == sign_url(uid)


def test_mapa_de_contas_pelo_email():
    m = engine.build_user_map([{"id": "r1", "email": "Dono@Casa.local"}, {"id": "r2", "email": "so@la.local"}],
                              [{"id": "l1", "email": "dono@casa.local"}])
    assert m == {"r1": "l1"}


def test_envelope_da_troca_e_autenticado():
    segredo = Fernet.generate_key().decode()
    corpo = service.seal(segredo, {"push": [1, 2]})
    assert service.unseal(segredo, corpo) == {"push": [1, 2]}
    with pytest.raises(service.SyncError):
        service.unseal(Fernet.generate_key().decode(), corpo)       # outro par
    with pytest.raises(service.SyncError):
        service.unseal(segredo, corpo[:-3] + b"xyz")                # adulterado
    tok = service.blob_token(segredo, "GET", "u1")
    assert service.check_blob_token(segredo, tok, "GET", "u1")
    assert not service.check_blob_token(segredo, tok, "PUT", "u1")
    assert not service.check_blob_token(segredo, tok, "GET", "u2")
