"""Assinatura ChatGPT → Codex: parse do OAuth colado e o adaptador de protocolo
(chat/completions ↔ Responses API). Puro/hermético — sem rede, sem DB."""
from __future__ import annotations

import json
from typing import ClassVar

import pytest

from aiworkspace.integration_routes import _safe_http_origin
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


def test_callback_state_identifica_usuario_sem_cookie_e_recusa_adulteracao():
    state = cs._encode_state("user-123")
    assert "user-123" not in state
    assert cs._decode_state_user(state) == "user-123"
    changed = state[:20] + ("A" if state[20] != "A" else "B") + state[21:]
    assert cs._decode_state_user(changed) == ""


def test_return_origin_preserva_host_real_e_remove_path():
    assert _safe_http_origin("http://192.168.1.200:3000/chat?x=1") == (
        "http://192.168.1.200:3000"
    )
    assert _safe_http_origin("https://ai.example.com/settings") == "https://ai.example.com"
    assert _safe_http_origin("http://user:pass@example.com") == ""
    assert _safe_http_origin("javascript:alert(1)") == ""


@pytest.mark.asyncio
async def test_callback_recupera_origem_do_fluxo_salvo(monkeypatch):
    state = cs._encode_state("user-123")

    async def fake_get(_db, key):
        assert key == "chatgpt_auth:user-123"
        return {"return_origin": "http://192.168.1.200:3000"}

    async def fake_finish(_db, user_id, parsed):
        assert user_id == "user-123"
        assert parsed == {"code": "oauth-code", "state": state}
        return {"connected": True}

    monkeypatch.setattr(cs, "get_setting", fake_get)
    monkeypatch.setattr(cs, "_finish_auth", fake_finish)
    out, user_id, origin = await cs.finish_auth_callback(None, "oauth-code", state)
    assert out == {"connected": True}
    assert user_id == "user-123"
    assert origin == "http://192.168.1.200:3000"


@pytest.mark.asyncio
async def test_device_begin_guarda_fluxo_e_devolve_codigo(monkeypatch):
    saved = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "device_auth_id": "device-1",
                "user_code": "ABCD-1234",
                "interval": "5",
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            assert url == cs.DEVICE_CODE_URL
            assert kwargs["json"] == {"client_id": cs.CLIENT_ID}
            return Response()

    async def fake_set(_db, key, value):
        saved[key] = value

    monkeypatch.setattr(cs.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(cs, "set_setting", fake_set)
    out = await cs.begin_device_auth(None, "user-1")

    assert out == {
        "url": cs.DEVICE_VERIFY_URL,
        "user_code": "ABCD-1234",
        "interval": 5,
    }
    assert saved["chatgpt_auth:user-1"]["device_auth_id"] == "device-1"


@pytest.mark.asyncio
async def test_device_poll_troca_codigo_sem_callback_local(monkeypatch):
    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "authorization_code": "oauth-code",
                "code_challenge": "unused-here",
                "code_verifier": "verifier-from-openai",
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **kwargs):
            assert url == cs.DEVICE_TOKEN_URL
            assert kwargs["json"]["device_auth_id"] == "device-1"
            return Response()

    async def fake_get(_db, key):
        assert key == "chatgpt_auth:user-1"
        return {
            "type": "device",
            "device_auth_id": "device-1",
            "user_code": "ABCD-1234",
            "created": 9_999_999_999,
        }

    async def fake_exchange(_db, user_id, code, verifier, redirect_uri):
        assert (user_id, code, verifier) == (
            "user-1",
            "oauth-code",
            "verifier-from-openai",
        )
        assert redirect_uri == cs.DEVICE_REDIRECT_URI
        return {"connected": True}

    monkeypatch.setattr(cs.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(cs, "get_setting", fake_get)
    monkeypatch.setattr(cs, "_exchange_code", fake_exchange)
    assert await cs.poll_device_auth(None, "user-1") == {"connected": True}


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
    with pytest.raises(RuntimeError, match="Assinaturas"):
        cx._user_id_from_key("sk-or-v1-abc")
    assert cx._user_id_from_key("codex:u-1") == "u-1"


def test_is_codex_model_prefix_rule():
    assert cx.is_codex_model("codex/gpt-5") is True
    assert cx.is_codex_model("ollama/llama3") is False
    assert cx.is_codex_model("anthropic/claude-sonnet-4.5") is False
    assert cx.is_codex_model(None) is False


class _FakeResponse:
    status_code = 200

    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for event in self.events:
            yield "data: " + json.dumps(event)
        yield "data: [DONE]"


class _FakeClient:
    events: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _FakeResponse(self.events)


@pytest.fixture
def fake_codex_stream(monkeypatch):
    async def access(_user_id):
        return "token", None

    async def instructions():
        return "instr"

    monkeypatch.setattr(cs, "get_access", access)
    monkeypatch.setattr(cs, "get_instructions", instructions)
    monkeypatch.setattr(cx.httpx, "AsyncClient", _FakeClient)
    _FakeClient.events = []
    return _FakeClient


@pytest.mark.asyncio
async def test_incomplete_terminal_preserves_usage_then_raises(fake_codex_stream):
    fake_codex_stream.events = [{
        "type": "response.incomplete",
        "response": {
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "total_tokens": 14,
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        },
    }]
    chunks = []
    with pytest.raises(RuntimeError, match="max_output_tokens"):
        async for chunk in cx.stream_chat(
            "codex:user-1", "codex/gpt-5", [{"role": "user", "content": "oi"}]
        ):
            chunks.append(chunk)
    assert chunks[-1]["usage"]["total_tokens"] == 14
    assert chunks[-1]["usage"]["reasoning_tokens"] == 2


@pytest.mark.asyncio
async def test_missing_terminal_event_is_not_silent_success(fake_codex_stream):
    fake_codex_stream.events = [{
        "type": "response.output_text.delta", "delta": "parcial",
    }]
    chunks = []
    with pytest.raises(RuntimeError, match="sem evento terminal"):
        async for chunk in cx.stream_chat(
            "codex:user-1", "codex/gpt-5", [{"role": "user", "content": "oi"}]
        ):
            chunks.append(chunk)
    assert chunks[0]["choices"][0]["delta"]["content"] == "parcial"
