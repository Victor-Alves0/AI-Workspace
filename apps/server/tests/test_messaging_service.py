"""Testes do messaging_service e dos parsers de leitura (Evolution/Discord).

Sem rede: os clientes httpx são substituídos por fakes assíncronos. Cobre a
normalização de payloads (parte frágil — varia entre versões) e a agregação de
conexões usada pela config por-modelo."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace.integrations import discord_api
from aiworkspace.integrations import messaging_service as ms
from aiworkspace.integrations import whatsapp_evolution as evo


# --------------------------------------------------------------------------- #
# Fakes de httpx assíncrono
# --------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, status=200, json_data=None):
        self.status_code = status
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    """Cliente httpx async fake: roteia por (method, path) e grava a última request."""
    last: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, path, json=None):
        FakeAsyncClient.last = {"method": "POST", "path": path, "json": json}
        return FakeAsyncClient.route("POST", path, json)

    async def get(self, url, headers=None, params=None):
        FakeAsyncClient.last = {"method": "GET", "url": url, "headers": headers, "params": params}
        return FakeAsyncClient.route("GET", url, params)


# --------------------------------- _to_wa_jid --------------------------------

def test_to_wa_jid_passthrough_and_digits():
    assert ms._to_wa_jid("5511999999999@s.whatsapp.net") == "5511999999999@s.whatsapp.net"
    assert ms._to_wa_jid("123@g.us") == "123@g.us"
    assert ms._to_wa_jid("+55 (11) 99999-9999") == "5511999999999@s.whatsapp.net"
    assert ms._to_wa_jid("") == ""
    assert ms._to_wa_jid("sem-numero") == ""


# --------------------------------- evolution ---------------------------------

async def test_find_chats_normalizes_and_drops_broadcast(monkeypatch):
    def route(m, path, body):
        return FakeResp(200, [
            {"remoteJid": "5511@s.whatsapp.net", "pushName": "Ana"},
            {"id": "999@g.us", "subject": "Família"},
            {"remoteJid": "status@broadcast"},
        ])
    FakeAsyncClient.route = staticmethod(route)
    monkeypatch.setattr(evo, "_client", lambda: FakeAsyncClient())
    out = await evo.find_chats("inst1")
    jids = [c["jid"] for c in out]
    assert "status@broadcast" not in jids
    assert {"jid": "5511@s.whatsapp.net", "name": "Ana", "is_group": False} in out
    grp = next(c for c in out if c["jid"] == "999@g.us")
    assert grp["is_group"] is True and grp["name"] == "Família"


async def test_find_messages_extracts_text_and_orders(monkeypatch):
    def route(m, path, body):
        return FakeResp(200, {"messages": {"records": [
            {"key": {"fromMe": True, "id": "b"}, "message": {"conversation": "oi"}, "messageTimestamp": 200},
            {"key": {"fromMe": False, "id": "a"}, "message": {"extendedTextMessage": {"text": "tudo?"}},
             "pushName": "Ana", "messageTimestamp": 100},
        ]}})
    FakeAsyncClient.route = staticmethod(route)
    monkeypatch.setattr(evo, "_client", lambda: FakeAsyncClient())
    out = await evo.find_messages("inst1", "5511@s.whatsapp.net", limit=10)
    assert [m["text"] for m in out] == ["tudo?", "oi"]  # ordenado por ts asc
    assert out[0]["from_me"] is False and out[0]["sender_name"] == "Ana"
    assert out[1]["from_me"] is True


async def test_find_contacts_filters_by_query(monkeypatch):
    def route(m, path, body):
        return FakeResp(200, [
            {"remoteJid": "111@s.whatsapp.net", "pushName": "João Silva"},
            {"remoteJid": "222@s.whatsapp.net", "pushName": "Maria"},
            {"remoteJid": "333@g.us", "pushName": "Grupo"},  # grupo é ignorado em contatos
        ])
    FakeAsyncClient.route = staticmethod(route)
    monkeypatch.setattr(evo, "_client", lambda: FakeAsyncClient())
    out = await evo.find_contacts("inst1", query="joão")
    assert len(out) == 1 and out[0]["jid"] == "111@s.whatsapp.net"


# --------------------------------- discord -----------------------------------

async def test_discord_get_messages_reverses_to_chronological(monkeypatch):
    def route(m, url, params):
        return FakeResp(200, [
            {"id": "2", "content": "segundo", "timestamp": "t2", "author": {"id": "u1", "username": "ana"}},
            {"id": "1", "content": "primeiro", "timestamp": "t1", "author": {"id": "u2", "username": "bot", "bot": True}},
        ])
    FakeAsyncClient.route = staticmethod(route)
    monkeypatch.setattr(discord_api.httpx, "AsyncClient", FakeAsyncClient)
    out = await discord_api.get_messages("tok", "chan1", limit=5)
    assert [m["text"] for m in out] == ["primeiro", "segundo"]  # API devolve recentes 1º
    assert out[1]["author"] == "ana" and out[0]["is_bot"] is True
    assert FakeAsyncClient.last["params"]["limit"] == 5


async def test_discord_get_messages_http_error(monkeypatch):
    def route(m, url, params):
        return FakeResp(403, {"message": "Missing Access"})
    FakeAsyncClient.route = staticmethod(route)
    monkeypatch.setattr(discord_api.httpx, "AsyncClient", FakeAsyncClient)
    with pytest.raises(discord_api.DiscordError) as e:
        await discord_api.get_messages("tok", "chan1")
    assert "Missing Access" in str(e.value)


# --------------------------------- gather_accounts ---------------------------

class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeDB:
    """db.scalars(select(Model)...) → devolve as linhas conforme a tabela consultada."""
    def __init__(self, by_model):
        self._by_model = by_model

    async def scalars(self, stmt):
        # a primeira entidade do select identifica o Model
        ent = stmt.column_descriptions[0]["entity"]
        return _FakeScalarResult(self._by_model.get(ent, []))


async def test_gather_accounts_flattens_platforms():
    from aiworkspace.models import DiscordConnection, TelegramConnection, WhatsAppConnection
    db = _FakeDB({
        WhatsAppConnection: [SimpleNamespace(id="wa1", label="", instance="inst", bot_username="")],
        TelegramConnection: [SimpleNamespace(id="tg1", label="Meu bot", bot_username="mybot")],
        DiscordConnection: [SimpleNamespace(id="dc1", label="", bot_username="dcbot", instance="")],
    })
    out = await ms.gather_accounts(db, "user1")
    by_plat = {a["platform"]: a for a in out}
    assert by_plat["whatsapp"]["label"] == "inst"       # cai p/ instance quando sem label
    assert by_plat["telegram"]["label"] == "Meu bot"    # label vence
    assert by_plat["discord"]["label"] == "dcbot"       # cai p/ bot_username
    assert {a["platform"] for a in out} == {"whatsapp", "telegram", "discord"}
