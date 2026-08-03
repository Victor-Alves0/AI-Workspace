"""Métrica de contexto: usage.context_tokens = base da 1ª chamada, NÃO a soma cumulativa.

Trava o fix documentado: `prompt_tokens` é a SOMA das N iterações do loop agêntico (o
contexto é reenviado a cada volta), então usá-lo no medidor mostrava "contexto estourado"
com contexto real pequeno. `context_tokens` guarda o prompt da 1ª chamada = tamanho real
do turno. O medidor e a auto-compactação dependem disso."""
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


def _tool_chunk(pt):
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "web.search.query", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": 5, "total_tokens": pt + 5}}


def _text_chunk(pt):
    return {"choices": [{"delta": {"content": "pronto"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": 3, "total_tokens": pt + 3}}


def test_context_tokens_is_first_call_not_cumulative():
    state = {"n": 0}

    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        state["n"] += 1
        if state["n"] == 1:
            yield _tool_chunk(1000)   # 1ª chamada: contexto real = 1000
        else:
            yield _text_chunk(1500)   # 2ª chamada: contexto cresceu com o tool_result

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="faça",
                chat_system_prompt=None, params={}, session=TurnSession(user_id="u"),
                sift=FakeSift(), use_tools=True)]
        events = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig

    done = [e for e in events if e["type"] == "done"][0]
    u = done["usage"]
    assert u["context_tokens"] == 1000, f"context_tokens deveria ser a base da 1ª chamada, veio {u.get('context_tokens')}"
    assert u["prompt_tokens"] == 2500, f"prompt_tokens é cumulativo (1000+1500), veio {u.get('prompt_tokens')}"
