"""Testes da lógica de resolução do turno (memória, conhecimento, code mode).

Usam SimpleNamespace: as funções só leem atributos (chat/model_config/user)."""
from __future__ import annotations

from types import SimpleNamespace

from aiworkspace.chat import turn_setup as ts


def _user(profile=None):
    return SimpleNamespace(profile=profile or {}, email="u@x.com", id="uid")


def _chat(**over):
    d = dict(memory_config=None, knowledge_config=None, folder_id=None)
    d.update(over)
    return SimpleNamespace(**d)


def _mc(**over):
    d = dict(id="mc1", capabilities={}, base_model="deepseek/x",
             tools_enabled=False, code_mode=False)
    d.update(over)
    return SimpleNamespace(**d)


# ------------------------------ memória --------------------------------------

def test_memory_disabled_by_default():
    """Memória é OPT-IN: sem config, leitura/escrita desligadas."""
    read, write, _ = ts._resolve_memory(_chat(), _mc(), _user())
    assert write == "off"
    assert not any(read.values())


def test_memory_enabled_by_chat_overrides():
    chat = _chat(memory_config={"enabled": True, "write": "chat",
                                "read": {"global": True, "model": False, "chat": True}})
    read, write, _ = ts._resolve_memory(chat, _mc(), _user())
    assert write == "chat"
    assert read["global"] is True and read["chat"] is True


def test_memory_chat_disables_over_model_enable():
    """`enabled: False` no nível mais específico (chat) zera tudo."""
    mc = _mc(capabilities={"memory": {"enabled": True, "write": "model"}})
    chat = _chat(memory_config={"enabled": False})
    read, write, _ = ts._resolve_memory(chat, mc, _user())
    assert write == "off"
    assert not any(read.values())


def test_mem_banks_union_and_gated_off():
    mc = _mc(capabilities={"memory": {"enabled": True, "banks": ["b1", "b2"]}})
    assert set(ts._mem_banks(_chat(), mc, _user())) == {"b1", "b2"}
    # memória desligada no chat → sem bancos
    chat = _chat(memory_config={"enabled": False})
    assert ts._mem_banks(chat, mc, _user()) == []


def test_mem_agent_id_prefers_model_config():
    assert ts._mem_agent_id(_mc(), "deepseek/x") == "mc1"
    assert ts._mem_agent_id(None, "deepseek/x") == "base:deepseek/x"


# ---------------------------- conhecimento -----------------------------------

def test_knowledge_union_profile_model_chat():
    """As bases acopladas são a UNIÃO de perfil + modelo + chat (sem duplicar)."""
    user = _user({"knowledge": {"bases": ["A"]}})
    mc = _mc(capabilities={"knowledge": {"bases": ["B"]}})
    chat = _chat(knowledge_config={"bases": ["B", "C"], "mode": "tool", "k": 3})
    res = ts._resolve_knowledge(chat, mc, user)
    assert set(res["bases"]) == {"A", "B", "C"}
    assert res["mode"] == "tool"  # camada mais específica
    assert res["k"] == 3


def test_knowledge_disabled_most_specific_wins():
    user = _user({"knowledge": {"bases": ["A"]}})
    chat = _chat(knowledge_config={"enabled": False})
    res = ts._resolve_knowledge(chat, _mc(), user)
    assert res["bases"] == []


def test_knowledge_ephemeral_chat_none():
    user = _user({"knowledge": {"bases": ["A"]}})
    res = ts._resolve_knowledge(None, _mc(), user)
    assert res["bases"] == ["A"]  # chat=None usa só perfil+modelo


# ------------------------------ code mode ------------------------------------

def test_code_mode_requires_tools_and_flag(monkeypatch):
    monkeypatch.setattr(ts, "get_settings", lambda: SimpleNamespace(allow_code_mode=True))
    assert ts._code_mode(_mc(tools_enabled=True, code_mode=True)) is True
    assert ts._code_mode(_mc(tools_enabled=False, code_mode=True)) is False
    assert ts._code_mode(_mc(tools_enabled=True, code_mode=False)) is False
    assert ts._code_mode(None) is False


def test_code_mode_off_when_global_killswitch(monkeypatch):
    """Off-switch global (allow_code_mode=False) desliga mesmo com flag do modelo."""
    monkeypatch.setattr(ts, "get_settings", lambda: SimpleNamespace(allow_code_mode=False))
    assert ts._code_mode(_mc(tools_enabled=True, code_mode=True)) is False
