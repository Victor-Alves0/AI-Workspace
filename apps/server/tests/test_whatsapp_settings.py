"""Ajustes por número do WhatsApp: memória no formato do modelo, limpeza do que a
tela manda e o número que só fica disponível para a IA do chat (não atende)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from aiworkspace.integrations import whatsapp_service as ws
from aiworkspace.whatsapp_routes import _clean_memory


def _conn(**kw):
    base = {"id": uuid.uuid4(), "memory": "local", "memory_config": None, "provider": "evolution",
            "auto_reply": True, "enabled": True}
    return SimpleNamespace(**{**base, **kw})


def test_memoria_antiga_continua_valendo():
    chat = SimpleNamespace(id=uuid.uuid4())
    _, _, local = ws._memory_setup(_conn(memory="local"), chat, None, "m")
    assert local.write == "chat" and local.read == {"global": False, "model": False, "chat": True}
    _, _, glob = ws._memory_setup(_conn(memory="global"), chat, None, "m")
    assert glob.write == "model" and glob.read["global"]


def test_memoria_configurada_como_a_do_modelo():
    chat = SimpleNamespace(id=uuid.uuid4())
    cfg = {"enabled": True, "write": "bank:b1", "read": {"global": True, "model": False, "chat": True},
           "banks": ["b1"]}
    mc = SimpleNamespace(id=uuid.uuid4(), capabilities={"memory": {"banks": ["b0"]}})
    _, agent, mem = ws._memory_setup(_conn(memory_config=cfg), chat, mc, "m")
    assert agent == str(mc.id)
    assert mem.write == "bank:b1"
    assert mem.read == {"global": True, "model": False, "chat": True}
    assert mem.banks == ["b0", "b1"]

    _, _, off = ws._memory_setup(_conn(memory_config={"enabled": False}), chat, None, "m")
    assert off.write == "off" and not any(off.read.values())


def test_limpeza_da_memoria():
    assert _clean_memory(None) is None
    c = _clean_memory({"write": "qualquer", "read": {"global": False}, "banks": [1, 2]})
    assert c == {"enabled": True, "write": "chat",
                 "read": {"global": False, "model": False, "chat": True}, "banks": ["1", "2"]}
    assert _clean_memory({"write": "bank:x"})["write"] == "bank:x"


async def test_numero_que_nao_atende_so_guarda(monkeypatch):
    conn = _conn(provider="official", auto_reply=False)
    gravadas, enviadas = [], []

    class FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _model, _id):
            return conn

    async def fake_record(c, rows):
        gravadas.extend(rows)

    async def fake_submit(**kw):
        enviadas.append(kw)

    monkeypatch.setattr(ws, "SessionLocal", FakeDB)
    monkeypatch.setattr(ws, "record_official", fake_record)
    monkeypatch.setattr(ws.inbound_batch, "submit", fake_submit)
    import time
    await ws.handle_incoming(conn.id, [{"jid": "5511", "text": "oi", "msg_id": f"x{uuid.uuid4().hex}",
                                        "sender_name": "Ana", "ts": int(time.time())}])
    assert [r["text"] for r in gravadas] == ["oi"]
    assert enviadas == []  # ninguém responde sozinho


# --------------------------------------------------------------------------- #
# A tool com vários números: ler olha em todos; enviar pergunta qual
# --------------------------------------------------------------------------- #
CONTAS = [{"id": "w1", "platform": "whatsapp", "label": "Loja"},
          {"id": "w2", "platform": "whatsapp", "label": "Pessoal"},
          {"id": "t1", "platform": "telegram", "label": "Bot"}]


def _msg(monkeypatch, **params):
    import json

    from sift import Sift

    from aiworkspace.integrations import messaging_service as ms
    from aiworkspace.tools import sift_service

    async def fake_new(platform, cid, hours, unanswered, limit):
        if cid == "w2":
            raise ms.MessagingError("a conexão Pessoal está pausada.")
        return [{"chat": "5511", "name": "Ana", "unanswered": True, "last_ts": 10, "messages": []}]

    async def fake_list(platform, cid, query, limit):
        return [{"id": f"{cid}-chat", "name": "x"}]

    monkeypatch.setattr(ms, "new_messages", fake_new)
    monkeypatch.setattr(ms, "list_chats", fake_list)
    s = Sift()
    cfg = sift_service.messaging_config_from_secrets("u1", CONTAS, {})
    sift_service._register_builtins(s, sift_service.SearchConfig(), {"messaging.chat.manage"}, messaging_cfg=cfg)
    s.build_index()
    raw = s.dispatch("execute_tool", {"path": "messaging.chat.manage", "params": params})
    return json.loads(raw) if isinstance(raw, str) else raw


def test_mensagens_novas_olha_todos_os_whatsapps(monkeypatch):
    out = _msg(monkeypatch, action="new_messages")
    assert [c["account"] for c in out["conversations"]] == ["Loja"]
    assert "Pessoal" in out["notes"][0]  # a conta fora do ar avisa, não cala as outras


def test_listar_sem_conta_junta_todas(monkeypatch):
    out = _msg(monkeypatch, action="list_chats")
    assert {c["account"] for c in out["chats"]} == {"Loja", "Pessoal", "Bot"}


def test_enviar_sem_conta_pergunta_qual(monkeypatch):
    out = _msg(monkeypatch, action="send_message", chat="5511", text="oi")
    assert out.get("kind") and "question" in out


def test_conta_nomeada_le_so_ela(monkeypatch):
    out = _msg(monkeypatch, action="list_chats", account="Loja")
    assert out["account"] == "Loja" and [c["id"] for c in out["chats"]] == ["w1-chat"]
