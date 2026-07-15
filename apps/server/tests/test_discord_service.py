"""Testes puros do discord_service: parse do evento + filtros (sem DB/rede)."""
from __future__ import annotations

from types import SimpleNamespace

from aiworkspace.integrations import discord_service as ds


def _event(**over):
    d = {
        "id": "100", "channel_id": "chan1", "content": "olá bot",
        "author": {"id": "u1", "username": "victor", "bot": False},
        "guild_id": "g1", "mentions": [],
    }
    d.update(over)
    return d


def _conn(app_id="bot9", **filters):
    return SimpleNamespace(app_id=app_id, filters=filters)


# --------------------------------- parse -------------------------------------

def test_parse_basic_guild_message():
    m = ds.parse_message(_event())
    assert m["channel_id"] == "chan1" and m["text"] == "olá bot"
    assert m["is_dm"] is False and m["sender_id"] == "u1" and m["username"] == "victor"


def test_parse_dm_has_no_guild():
    m = ds.parse_message(_event(guild_id=None))
    assert m["is_dm"] is True


def test_parse_ignores_bots():
    assert ds.parse_message(_event(author={"id": "b", "username": "x", "bot": True})) is None


def test_parse_ignores_empty_content():
    assert ds.parse_message(_event(content="   ")) is None


def test_parse_collects_mentions():
    m = ds.parse_message(_event(mentions=[{"id": "bot9"}, {"id": "u2"}]))
    assert m["mention_ids"] == {"bot9", "u2"}


# --------------------------------- filters -----------------------------------

def test_dm_always_passes_even_with_mention_only():
    m = ds.parse_message(_event(guild_id=None))
    ok, _ = ds.passes_filters(_conn(mention_only=True), m)
    assert ok is True


def test_guild_mention_only_blocks_without_mention():
    m = ds.parse_message(_event(mentions=[{"id": "u2"}]))
    ok, reason = ds.passes_filters(_conn(mention_only=True), m)
    assert ok is False and "menção" in reason


def test_guild_mention_only_passes_with_mention():
    m = ds.parse_message(_event(mentions=[{"id": "bot9"}]))
    ok, _ = ds.passes_filters(_conn(mention_only=True), m)
    assert ok is True


def test_guilds_disabled_blocks_server_messages():
    m = ds.parse_message(_event())
    ok, reason = ds.passes_filters(_conn(guilds=False, mention_only=False), m)
    assert ok is False and "servidores" in reason


def test_block_list_by_username():
    m = ds.parse_message(_event())
    ok, reason = ds.passes_filters(_conn(mention_only=False, block=["victor"]), m)
    assert ok is False and "bloqueado" in reason


def test_allow_list_excludes_others():
    m = ds.parse_message(_event())
    ok, reason = ds.passes_filters(_conn(mention_only=False, allow=["outrapessoa"]), m)
    assert ok is False and "permitidos" in reason


def test_trigger_prefix_required():
    m = ds.parse_message(_event(content="sem prefixo"))
    ok, reason = ds.passes_filters(_conn(mention_only=False, trigger="!ai"), m)
    assert ok is False and "gatilho" in reason


# ------------------------------ strip mention --------------------------------

def test_strip_mention_removes_leading_bot_ping():
    assert ds._strip_mention("<@bot9> qual a hora?", "bot9") == "qual a hora?"
    assert ds._strip_mention("<@!bot9>  oi", "bot9") == "oi"


def test_strip_mention_keeps_text_without_ping():
    assert ds._strip_mention("só um texto", "bot9") == "só um texto"
