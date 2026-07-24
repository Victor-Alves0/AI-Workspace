"""Assinatura ChatGPT → Codex: parse do OAuth colado e o adaptador de protocolo
(chat/completions ↔ Responses API). Puro/hermético — sem rede, sem DB."""
from __future__ import annotations

from aiworkspace.integrations import chatgpt_service as cs
from aiworkspace.providers import chatgpt_codex as cx


# --------------------------------------------------------------------------- #
# parse_pasted — o usuário cola a URL de redirect inteira (ou só o code)
# --------------------------------------------------------------------------- #
def test_parse_pasted_full_redirect_url():
    out = cs.parse_pasted("http://localhost:1455/auth/callback?code=abc123&state=st9")
    assert out == {"code": "abc123", "state": "st9"}


def test_parse_pasted_code_hash_state_and_bare_code():
    assert cs.parse_pasted("abc#st") == {"code": "abc", "state": "st"}
    assert cs.parse_pasted("abc123") == {"code": "abc123"}
    assert cs.parse_pasted("  ") == {}


def test_pkce_pair_is_s256_shaped():
    import base64
    import hashlib
    verifier, challenge = cs._pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")


# --------------------------------------------------------------------------- #
# Tradução de mensagens → input items
# --------------------------------------------------------------------------- #
def test_build_input_maps_roles():
    items = cx.build_input([
        {"role": "system", "content": "seja breve"},
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "olá!"},
    ])
    assert [i.get("role") for i in items] == ["developer", "user", "assistant"]
    assert items[0]["content"][0]["type"] == "input_text"
    assert items[2]["content"][0]["type"] == "output_text"  # assistant fala em output


def test_build_input_maps_tool_round_trip():
    """tool_calls do assistant → function_call; role tool → function_call_output
    com o MESMO call_id — sem isso o backend não casa a resposta da tool."""
    items = cx.build_input([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "web__search", "arguments": '{"q":"x"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'},
    ])
    assert items[0] == {"type": "function_call", "call_id": "c1",
                        "name": "web__search", "arguments": '{"q":"x"}'}
    assert items[1] == {"type": "function_call_output", "call_id": "c1",
                        "output": '{"ok":true}'}


def test_build_input_flattens_multimodal_text_parts():
    items = cx.build_input([{"role": "user", "content": [
        {"type": "text", "text": "descreva"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]}])
    assert items[0]["content"][0]["text"] == "descreva"  # imagem fica de fora


def test_convert_tools_to_responses_format():
    out = cx.convert_tools([{"type": "function", "function": {
        "name": "f", "description": "d", "parameters": {"type": "object"}}}])
    assert out == [{"type": "function", "name": "f", "description": "d",
                    "parameters": {"type": "object"}, "strict": False}]


def test_build_payload_strips_prefix_and_sets_codex_contract():
    p = cx.build_payload("codex/gpt-5-codex", [{"role": "user", "content": "oi"}],
                         instructions="INSTR", tools=None, params={"reasoning": {"effort": "high"}})
    assert p["model"] == "gpt-5-codex"
    assert p["instructions"] == "INSTR"
    assert p["store"] is False and p["stream"] is True
    assert p["reasoning"]["effort"] == "high"
    assert "tools" not in p  # sem tools => sem tool_choice


def test_api_key_sentinel_is_required():
    import pytest
    with pytest.raises(RuntimeError, match="Assinaturas"):
        cx._user_id_from_key("sk-or-v1-abc")
    assert cx._user_id_from_key("codex:u-1") == "u-1"


def test_is_codex_model_prefix_rule():
    assert cx.is_codex_model("codex/gpt-5") is True
    assert cx.is_codex_model("ollama/llama3") is False
    assert cx.is_codex_model("anthropic/claude-sonnet-4.5") is False
    assert cx.is_codex_model(None) is False
