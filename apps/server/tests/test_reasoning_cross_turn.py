"""Raciocínio entre turnos e raciocínio cifrado do Codex.

Continuação de test_reasoning_details: além de voltar junto da chamada de ferramenta
(no mesmo turno), os blocos de raciocínio da resposta final ficam gravados na mensagem
e voltam no histórico dos próximos turnos — só para o modelo que os gerou.

No Codex (API Responses com `store: false`) a OpenAI não guarda nada entre chamadas:
o raciocínio só continua se o item cifrado voltar na entrada, e SEM o id (o item não
existe no servidor). É como o Codex CLI e o opencode fazem.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn
from aiworkspace.providers import chatgpt_codex as codex
from aiworkspace.providers import openrouter
from aiworkspace.providers import reasoning_details as rd

BLOCO = {"type": "reasoning.text", "text": "Pensei.", "format": "anthropic-claude-v1", "signature": "s1"}


# --------------------------------------------------------------------------- #
# Histórico                                                                    #
# --------------------------------------------------------------------------- #
def _msg(role, content, reasoning=None):
    return SimpleNamespace(role=role, content=content, reasoning=reasoning)


def test_mensagem_gravada_leva_os_blocos_marcados_com_o_modelo():
    m = _msg("assistant", "Resposta.", {"text": "Pensei. E mais.", "details": [BLOCO],
                                        "details_text": "Pensei.", "details_model": "anthropic/claude"})

    assert rd.history_entry(m) == {
        "role": "assistant", "content": "Resposta.",
        "reasoning_details": [BLOCO], "reasoning": "Pensei.", "_details_model": "anthropic/claude",
    }
    # mensagens antigas (sem blocos) e do usuário ficam como sempre foram
    assert rd.history_entry(_msg("assistant", "Oi.", {"text": "pensando"})) == {"role": "assistant", "content": "Oi."}
    assert rd.history_entry(_msg("user", "Olá")) == {"role": "user", "content": "Olá"}


def test_bloco_de_outro_modelo_nao_vai_e_a_marca_nunca_sai():
    """Trocar de modelo no meio do chat: a assinatura do Claude num pedido ao GPT (ou a
    outro Claude) derrubaria a chamada."""
    hist = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "A", "reasoning_details": [BLOCO], "reasoning": "x",
         "_details_model": "anthropic/claude"},
        {"role": "assistant", "content": "B", "reasoning_details": [BLOCO], "reasoning": "y",
         "_details_model": "openai/gpt"},
    ]

    enviado = rd.for_model(hist, "anthropic/claude")

    assert enviado[1]["reasoning_details"] == [BLOCO]
    assert "reasoning_details" not in enviado[2] and "reasoning" not in enviado[2]
    assert not any("_details_model" in m for m in enviado)
    assert "_details_model" in hist[1]  # a lista do chamador não muda


class _Resp:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        yield "data: " + json.dumps({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        yield "data: [DONE]"


class _Client:
    ultimo_json: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        _Client.ultimo_json = kwargs["json"]
        return _Resp()


@pytest.mark.asyncio
async def test_o_provedor_aplica_o_filtro_antes_de_enviar(monkeypatch):
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _Client)
    hist = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "A", "reasoning_details": [BLOCO], "_details_model": "outro/modelo"},
        {"role": "user", "content": "e agora?"},
    ]

    async for _ in openrouter.stream_chat("k", "deepseek/x", hist):
        pass

    enviadas = _Client.ultimo_json["messages"]
    assert all("_details_model" not in m and "reasoning_details" not in m for m in enviadas)


# --------------------------------------------------------------------------- #
# Gravação: os blocos da RESPOSTA FINAL                                        #
# --------------------------------------------------------------------------- #
class _FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"
    meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "weather.current.get",
                 "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        return '{"temp": 21}'


def _turno(voltas: list[list[dict]], *, use_tools=True):
    n = {"i": 0}

    def fake_stream(api_key, model, messages, *, tools=None, params=None, modalities=None, base_url=None):
        chunks = voltas[min(n["i"], len(voltas) - 1)]
        n["i"] += 1

        async def gen():
            for c in chunks:
                yield c
        return gen()

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="anthropic/claude", history=[], user_text="clima?",
                chat_system_prompt=None, params={}, use_tools=use_tools,
                sift=_FakeSift() if use_tools else None, session=TurnSession(user_id="u"))]
        eventos = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig
    return next(e for e in eventos if e["type"] == "done")


def _pensa(texto, sig):
    return {"choices": [{"delta": {"reasoning": texto, "reasoning_details": [
        {"type": "reasoning.text", "text": texto, "format": "anthropic-claude-v1", "signature": sig}]}}]}


def test_grava_so_os_blocos_da_resposta_final():
    """Os da volta que chamou a ferramenta já foram usados dentro do turno; o que volta
    no próximo turno é o raciocínio que produziu a resposta."""
    done = _turno([
        [_pensa("Vou buscar o clima.", "s-tool"),
         {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
             "name": "weather.current.get", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}],
        [_pensa("Tenho o dado.", "s-final"),
         {"choices": [{"delta": {"content": "Faz 21 °C."}, "finish_reason": "stop"}]}],
    ])

    r = done["reasoning"]
    assert [d["signature"] for d in r["details"]] == ["s-final"]
    assert r["details_text"] == "Tenho o dado."
    assert r["details_model"] == "anthropic/claude"
    assert r["text"] == "Vou buscar o clima.Tenho o dado."  # o "Pensou por…" segue inteiro


def test_so_cifrado_sem_texto_ainda_grava():
    """Gemini/OpenAI podem mandar só o bloco cifrado: sem texto para mostrar, mas com
    estado para continuar. A tela ignora raciocínio sem texto."""
    done = _turno([[
        {"choices": [{"delta": {"reasoning_details": [
            {"type": "reasoning.encrypted", "data": "OPACO", "format": "google-gemini-v1"}]}}]},
        {"choices": [{"delta": {"content": "Oi."}, "finish_reason": "stop"}]},
    ]], use_tools=False)

    assert done["reasoning"]["text"] == ""
    assert done["reasoning"]["details"][0]["data"] == "OPACO"


def test_resposta_promovida_do_raciocinio_nao_grava_blocos():
    """Quando o "raciocínio" era a resposta (fim de turno dentro dele), reenviá-lo como
    raciocínio duplicaria a resposta no histórico."""
    done = _turno([[
        {"choices": [{"delta": {"reasoning": "A porta abre.", "reasoning_details": [
            {"type": "reasoning.text", "text": "A porta abre.", "format": "unknown"}]},
            "turn_end_in_reasoning": True, "finish_reason": "stop"}]},
    ]], use_tools=False)

    assert done["content"] == "A porta abre."
    assert not (done["reasoning"] or {}).get("details")


def test_sem_raciocinio_nenhum_continua_none():
    done = _turno([[{"choices": [{"delta": {"content": "Oi."}, "finish_reason": "stop"}]}]], use_tools=False)

    assert done["reasoning"] is None


# --------------------------------------------------------------------------- #
# Codex                                                                        #
# --------------------------------------------------------------------------- #
def test_codex_pede_o_raciocinio_cifrado():
    payload = codex.build_payload("codex/gpt", [{"role": "user", "content": "oi"}], instructions="I")

    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]


def test_codex_reenvia_o_cifrado_sem_id_antes_da_chamada():
    msgs = [
        {"role": "user", "content": "clima?"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "function": {"name": "w", "arguments": "{}"}}],
         "reasoning_details": [
             {"type": "reasoning.encrypted", "format": "openai-responses-v1", "data": "ENC", "summary_text": "Vou ver."},
             {"type": "reasoning.encrypted", "format": "openai-responses-v1", "summary_text": "sem estado"},
             {"type": "reasoning.text", "format": "anthropic-claude-v1", "text": "de outro provedor"},
         ]},
        {"role": "tool", "tool_call_id": "c1", "content": "21"},
    ]

    itens = codex.build_input(msgs)

    tipos = [i.get("type") or i.get("role") for i in itens]
    assert tipos == ["user", "reasoning", "function_call", "function_call_output"]
    raciocinio = itens[1]
    assert raciocinio == {"type": "reasoning", "encrypted_content": "ENC",
                          "summary": [{"type": "summary_text", "text": "Vou ver."}]}
    assert "id" not in raciocinio


def test_codex_resposta_de_turno_anterior_tambem_leva_o_cifrado():
    itens = codex.build_input([
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "Olá!", "reasoning_details": [
            {"type": "reasoning.encrypted", "format": "openai-responses-v1", "data": "ENC", "summary_text": ""}]},
        {"role": "user", "content": "e aí?"},
    ])

    assert [i.get("type") or i.get("role") for i in itens] == ["user", "reasoning", "assistant", "user"]
    assert itens[1]["summary"] == []


class _CodexResp:
    status_code = 200
    eventos: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for ev in _CodexResp.eventos:
            yield "data: " + json.dumps(ev)


class _CodexClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _CodexResp()


@pytest.mark.asyncio
async def test_codex_item_de_raciocinio_vira_bloco_cifrado(monkeypatch):
    from aiworkspace.integrations import chatgpt_service

    async def acesso(uid):
        return "tok", None

    async def instrucoes():
        return "I"

    monkeypatch.setattr(chatgpt_service, "get_access", acesso)
    monkeypatch.setattr(chatgpt_service, "get_instructions", instrucoes)
    monkeypatch.setattr(codex.httpx, "AsyncClient", _CodexClient)
    _CodexResp.eventos = [
        {"type": "response.reasoning_summary_text.delta", "delta": "Vou ver."},
        {"type": "response.output_item.done", "item": {
            "type": "reasoning", "id": "rs_1", "encrypted_content": "ENC",
            "summary": [{"type": "summary_text", "text": "Vou ver."}]}},
        {"type": "response.output_text.delta", "delta": "Oi."},
        {"type": "response.completed", "response": {"usage": {}}},
    ]

    chunks = [c async for c in codex.stream_chat("codex:u", "codex/gpt", [{"role": "user", "content": "oi"}])]

    blocos = [d for c in chunks for ch in c.get("choices") or []
              for d in (ch.get("delta") or {}).get("reasoning_details") or []]
    assert blocos == [{"type": "reasoning.encrypted", "format": "openai-responses-v1",
                       "data": "ENC", "summary_text": "Vou ver."}]
