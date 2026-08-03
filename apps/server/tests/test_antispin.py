"""Anti-spin agnóstico de modelo: quando a MESMA (ferramenta,args,resultado) se repete,
o loop força a resposta final em vez de moer até o teto de iterações.

Reproduz o giro do turno 83 do Metabase (170 chamadas idênticas sem progresso) e prova
que agora ele para no limiar (agent_noprogress_repeats=3), com resposta real."""
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
        return '{"same": "result every time"}'  # SEMPRE o mesmo resultado


def _tool_chunk():
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "web.search.query", "arguments": '{"q":"x"}'}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}


def _text_chunk():
    return {"choices": [{"delta": {"content": "Concluído com base no que já apurei."},
            "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 8, "total_tokens": 108}}


def test_antispin_forces_final_answer_on_repeat():
    calls_with_tools = {"n": 0}

    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        # modelo realista: com tools oferecidas, insiste na MESMA chamada; sem tools
        # (anti-spin cortou), redige a resposta final.
        if tools is None:
            yield _text_chunk()
        else:
            calls_with_tools["n"] += 1
            yield _tool_chunk()

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="faça isso",
                chat_system_prompt=None, params={}, session=TurnSession(user_id="u"),
                sift=FakeSift(), use_tools=True)]
        events = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    done = [e for e in events if e["type"] == "done"][0]
    # anti-spin (limiar 3): exatamente 3 chamadas idênticas, aí forçou a resposta.
    # Sem anti-spin, moeria até o teto (8 no modo normal) e cairia no fallback.
    assert len(tool_calls) == 3, f"esperava 3 chamadas (limiar), veio {len(tool_calls)}"
    assert "Concluído" in done["content"], f"esperava resposta final real, veio: {done['content'][:80]!r}"
