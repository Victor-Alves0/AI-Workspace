"""Watchdog por tool-call: uma builtin que trava (ex.: taint sobre base gigante) NÃO
pode pendurar o turno inteiro. O dispatcher aborta após builtin_tool_timeout_seconds e
devolve um erro acionável ao modelo, que estreita o escopo — o turno se recupera.

Reproduz o travamento real do chat do Metabase (code.flow.analyze taint pendurado)."""
from __future__ import annotations

import asyncio
import time

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn


class FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"
    meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "code.flow.analyze",
                 "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        time.sleep(3.0)  # simula a análise que gira/trava
        return '{"ok": true}'


def _tool_chunk():
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "code.flow.analyze", "arguments": '{"action":"taint"}'}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}


def _text_chunk():
    return {"choices": [{"delta": {"content": "vou estreitar o escopo então"},
            "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 6, "total_tokens": 106}}


def test_hung_builtin_is_aborted_and_turn_recovers():
    calls = {"n": 0}

    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        calls["n"] += 1
        if calls["n"] == 1:
            yield _tool_chunk()   # pede a taint (que vai travar)
        else:
            yield _text_chunk()   # após o erro de timeout, redige

    s = orch.get_settings()
    old = s.builtin_tool_timeout_seconds
    s.builtin_tool_timeout_seconds = 1  # teto curto (int; o tool dorme 3s)

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    timing: dict = {}
    try:
        async def go():
            t0 = time.monotonic()
            evs = []
            async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="ataque",
                chat_system_prompt=None, params={}, sift=FakeSift(), use_tools=True,
                session=TurnSession(user_id="u")):
                evs.append(ev)
                if ev.get("type") == "done":
                    timing["recover"] = time.monotonic() - t0  # tempo REAL de recuperação
            return evs
        events = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig
        s.builtin_tool_timeout_seconds = old
    elapsed = timing.get("recover", 99)

    # o resultado da tool é o erro de timeout (não pendurou)
    results = [e for e in events if e.get("type") == "tool_result"]
    assert results, "esperava um tool_result"
    blob = str(results[0]["result"])
    assert "abortada" in blob and "1s" in blob, blob
    # o turno se recuperou e redigiu a resposta final
    done = [e for e in events if e["type"] == "done"][0]
    assert "estreitar" in done["content"], done["content"][:80]
    # abortou perto do teto (1s), antes do sleep de 3s
    assert elapsed < 2.5, f"deveria abortar em ~1s, levou {elapsed:.2f}s"
