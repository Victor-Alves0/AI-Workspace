"""Steer em tempo real: mensagem enviada DURANTE o turno é injetada entre iterações do
loop agêntico (sem reiniciar) e chega ao modelo como input prioritário.

Trava o mecanismo de `TurnSession.steer_drain` (ver run_turn, topo do loop): o loop drena
as mensagens de steer a cada volta, injeta como user prioritário, emite o evento `steer`
e reabre as tools se já haviam sido cortadas."""
from __future__ import annotations

import asyncio

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn


class FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"
    meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "web.search.query",
                 "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        return '{"ok": true}'


def _tool_chunk():
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "web.search.query", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}


def _text_chunk():
    return {"choices": [{"delta": {"content": "feito conforme a nova instrução"},
            "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 6, "total_tokens": 106}}


def test_steer_message_injected_between_iterations_and_reaches_model():
    loop_top = {"n": 0}
    calls = {"n": 0, "last_msgs": None}

    def steer_drain():
        loop_top["n"] += 1
        # dispara na 2ª iteração (depois que a 1ª tool rodou) — injeção entre voltas
        if loop_top["n"] == 2:
            return ["troque a abordagem para X"]
        return []

    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        calls["n"] += 1
        calls["last_msgs"] = [str(m.get("content") or "") for m in messages]
        if calls["n"] == 1:
            yield _tool_chunk()   # 1ª chamada: pede tool → loop continua
        else:
            yield _text_chunk()   # 2ª chamada (após steer): resposta final

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="faça a tarefa",
                chat_system_prompt=None, params={}, sift=FakeSift(), use_tools=True,
                session=TurnSession(user_id="u", steer_drain=steer_drain))]
        events = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig

    # emitiu o evento de steer p/ a UI
    steer_evs = [e for e in events if e.get("type") == "steer"]
    assert steer_evs and "troque a abordagem" in steer_evs[0]["text"], steer_evs
    # a instrução injetada chegou ao modelo na 2ª chamada (input prioritário)
    joined = "\n".join(calls["last_msgs"] or [])
    assert "interveio durante a geração" in joined
    assert "troque a abordagem para X" in joined
    # o turno terminou com a resposta que considera a nova instrução
    done = [e for e in events if e["type"] == "done"][0]
    assert "nova instrução" in done["content"]


def test_no_steer_drain_is_inert():
    # sem steer_drain (turno normal) nada é injetado nem emitido
    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        yield _text_chunk()

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="oi",
                chat_system_prompt=None, params={}, sift=FakeSift(), use_tools=True,
                session=TurnSession(user_id="u"))]
        events = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig
    assert not [e for e in events if e.get("type") == "steer"]
