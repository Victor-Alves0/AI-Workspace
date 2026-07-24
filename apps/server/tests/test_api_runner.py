"""Tradução do formato OpenAI para o turno da plataforma (`api.runner`).

Puro/hermético. O foco é o que quebra silenciosamente: mensagem multimodal,
histórico, precedência de parâmetros e o mapa modo-de-memória → escopo do mem0.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace.api import runner
from aiworkspace.api.deps import ApiError


def _key(**over):
    base = dict(id="kkk", memory={"mode": "none"}, limits={}, model_policy={"mode": "all"})
    base.update(over)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# parse_messages
# --------------------------------------------------------------------------- #

def test_parse_simple_conversation():
    p = runner.parse_messages([
        {"role": "system", "content": "seja breve"},
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "olá"},
        {"role": "user", "content": "tudo bem?"},
    ])
    assert p.system == "seja breve"
    assert p.user_text == "tudo bem?"
    assert p.history == [{"role": "user", "content": "oi"},
                         {"role": "assistant", "content": "olá"}]


def test_parse_joins_multiple_system_messages():
    p = runner.parse_messages([
        {"role": "system", "content": "regra 1"},
        {"role": "developer", "content": "regra 2"},
        {"role": "user", "content": "vai"},
    ])
    assert p.system == "regra 1\n\nregra 2"


def test_parse_uses_last_user_message_as_the_turn():
    """Mensagens depois da última do usuário não têm o que gerar — o turno atual é
    sempre a última fala do usuário."""
    p = runner.parse_messages([
        {"role": "user", "content": "primeira"},
        {"role": "assistant", "content": "resposta"},
        {"role": "user", "content": "segunda"},
    ])
    assert p.user_text == "segunda"
    assert len(p.history) == 2


def test_parse_multimodal_content_parts():
    p = runner.parse_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "o que é isto?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ],
    }])
    assert p.user_text == "o que é isto?"
    assert p.attachments == [
        {"type": "image", "name": "image", "url": "data:image/png;base64,AAA"}
    ]


def test_parse_audio_part_becomes_data_url():
    p = runner.parse_messages([{
        "role": "user",
        "content": [{"type": "input_audio", "input_audio": {"data": "QUJD", "format": "wav"}}],
    }])
    assert p.attachments[0]["url"].startswith("data:audio/wav;base64,QUJD")


def test_parse_rejects_empty_messages():
    with pytest.raises(ApiError) as exc:
        runner.parse_messages([])
    assert exc.value.code == "messages_required"


def test_parse_rejects_conversation_without_user():
    with pytest.raises(ApiError) as exc:
        runner.parse_messages([{"role": "assistant", "content": "sozinho"}])
    assert exc.value.code == "user_message_required"


def test_parse_rejects_tool_message_without_client_tools():
    """Mensagem `tool` sem `tools` é erro de uso: o loop da plataforma executa as
    ferramentas sozinho e nunca pediu nada ao cliente."""
    with pytest.raises(ApiError) as exc:
        runner.parse_messages([
            {"role": "user", "content": "oi"},
            {"role": "tool", "content": "{}"},
        ])
    assert exc.value.code == "unexpected_tool_message"


def test_parse_enforces_message_cap():
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(runner.MAX_MESSAGES + 1)]
    with pytest.raises(ApiError) as exc:
        runner.parse_messages(msgs)
    assert exc.value.code == "too_many_messages"


def test_parse_drops_empty_history_entries():
    p = runner.parse_messages([
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "algo"},
        {"role": "user", "content": "agora vai"},
    ])
    assert p.history == [{"role": "assistant", "content": "algo"}]


# --------------------------------------------------------------------------- #
# build_params
# --------------------------------------------------------------------------- #

def test_request_params_override_preset_params():
    rm = runner.ResolvedModel(api_key="k", base_url=None, base_model="x",
                              config=SimpleNamespace(params={"temperature": 0.2,
                                                             "max_tokens": 100}))
    out = runner.build_params(rm, {"temperature": 0.9})
    assert out["temperature"] == 0.9
    assert out["max_tokens"] == 100  # o que a requisição não manda, o preset mantém


def test_build_params_drops_structural_fields():
    """`model`/`messages`/`stream` são resolvidos pelo servidor: deixá-los passar
    permitiria trocar o modelo por um campo solto do corpo."""
    rm = runner.ResolvedModel(api_key="k", base_url=None, base_model="x", config=None)
    out = runner.build_params(rm, {"model": "outro", "messages": [], "stream": True,
                                   "temperature": 0.5})
    assert out == {"temperature": 0.5}


def test_build_params_accepts_max_completion_tokens_alias():
    rm = runner.ResolvedModel(api_key="k", base_url=None, base_model="x", config=None)
    assert runner.build_params(rm, {"max_completion_tokens": 42})["max_tokens"] == 42


# --------------------------------------------------------------------------- #
# Memória
# --------------------------------------------------------------------------- #

def test_memory_none_reads_and_writes_nothing():
    mem, agent, run = runner.memory_opts(_key(memory={"mode": "none"}), None)
    assert mem.write == "off"
    assert not any(mem.read.values())
    assert (agent, run) == (None, None)


def test_memory_persistent_uses_global_scope():
    mem, agent, run = runner.memory_opts(_key(memory={"mode": "persistent"}), None)
    assert mem.write == "global"
    assert mem.read["global"] is True
    assert (agent, run) == (None, None)


def test_memory_key_mode_is_isolated_per_key():
    a, _, _ = runner.memory_opts(_key(id="a", memory={"mode": "key"}), None)
    _, agent_a, _ = runner.memory_opts(_key(id="a", memory={"mode": "key"}), None)
    _, agent_b, _ = runner.memory_opts(_key(id="b", memory={"mode": "key"}), None)
    assert agent_a != agent_b
    assert a.read["global"] is False  # não enxerga a memória do app


def test_memory_shared_mode_is_the_same_scope_for_every_key():
    _, agent_a, _ = runner.memory_opts(_key(id="a", memory={"mode": "shared"}), None)
    _, agent_b, _ = runner.memory_opts(_key(id="b", memory={"mode": "shared"}), None)
    assert agent_a == agent_b == "api:shared"


def test_memory_end_user_requires_the_user_field():
    with pytest.raises(ApiError) as exc:
        runner.memory_opts(_key(memory={"mode": "end_user"}), None)
    assert exc.value.code == "end_user_required"


def test_memory_end_user_scopes_per_person():
    _, _, run_a = runner.memory_opts(_key(memory={"mode": "end_user"}), "ana")
    _, _, run_b = runner.memory_opts(_key(memory={"mode": "end_user"}), "bruno")
    assert run_a != run_b
