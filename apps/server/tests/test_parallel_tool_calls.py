"""Parallel tool calling: várias tools numa mensagem só rodam CONCORRENTES.

O modelo já agrupava as chamadas (o provedor permite por padrão), mas o loop
agêntico as executava em série — o que não custa tokens nem voltas, e sim TEMPO DE
PAREDE: ler 4 e-mails virava 4x a latência de rede enfileirada. Estes testes fixam o
comportamento novo e as invariantes que ele NÃO pode quebrar (ordem determinística,
isolamento de falha, e o caminho serial intacto)."""
from __future__ import annotations

import asyncio
import json
import time

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn

TOOL_SLEEP = 0.25  # cada tool "demora" isso; em série 3 delas = 0.75s


class SlowSift:
    """SIFT falsa cujo dispatch BLOQUEIA (como as nossas builtins reais, que são
    síncronas e rodam no threadpool). É o que permite medir série vs paralelo."""
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"

    def __init__(self, fail: set[str] | None = None):
        self.meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}
        self._fail = fail or set()

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        time.sleep(TOOL_SLEEP)
        if args.get("id") in self._fail:
            raise RuntimeError("boom")
        return json.dumps({"id": args.get("id")})


def _multi_tool_chunk(ids: list[str]) -> dict:
    """Uma mensagem do assistant com VÁRIAS tool_calls (parallel tool calling)."""
    return {"choices": [{
        "delta": {"tool_calls": [
            {"index": i, "id": f"call_{tid}",
             "function": {"name": "t", "arguments": json.dumps({"id": tid})}}
            for i, tid in enumerate(ids)
        ]},
        "finish_reason": "tool_calls",
    }]}


def _final_chunk() -> dict:
    return {"choices": [{"delta": {"content": "pronto"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}


def _fake_stream(scripts):
    state = {"i": 0, "messages": []}

    async def fake(api_key, model, messages, *, tools=None, params=None,
                   modalities=None, base_url=None):
        state["messages"].append([dict(m) for m in messages])
        script = scripts[min(state["i"], len(scripts) - 1)]
        state["i"] += 1
        for ch in script:
            yield ch

    return fake, state


def _kwargs(sift):
    return dict(api_key="k", model="m", history=[], user_text="oi",
                chat_system_prompt=None, params={},
                session=TurnSession(user_id="u"), sift=sift, use_tools=True)


async def _collect(gen):
    return [ev async for ev in gen]


# ------------------------------- testes --------------------------------------

async def test_three_tools_run_concurrently(monkeypatch):
    fake, _ = _fake_stream([[_multi_tool_chunk(["a", "b", "c"])], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)

    t0 = time.perf_counter()
    events = await _collect(run_turn(**_kwargs(SlowSift())))
    elapsed = time.perf_counter() - t0

    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 3
    # em série seriam ~3 x TOOL_SLEEP; concorrentes, ~1 x (folga generosa p/ CI lento)
    assert elapsed < TOOL_SLEEP * 2, f"executou em série ({elapsed:.2f}s)"


async def test_results_keep_the_original_order(monkeypatch):
    """A execução embaralha, mas a UI e o histórico têm de sair determinísticos."""
    fake, state = _fake_stream([[_multi_tool_chunk(["a", "b", "c"])], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_kwargs(SlowSift())))

    order = [e["result"]["id"] for e in events if e["type"] == "tool_result"]
    assert order == ["a", "b", "c"]
    # e as mensagens devolvidas ao modelo casam id<->tool_call_id, na ordem
    second_call = state["messages"][1]
    tool_msgs = [m for m in second_call if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_a", "call_b", "call_c"]
    assert [json.loads(m["content"])["id"] for m in tool_msgs] == ["a", "b", "c"]


async def test_calls_are_announced_before_results(monkeypatch):
    fake, _ = _fake_stream([[_multi_tool_chunk(["a", "b"])], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    types = [e["type"] for e in await _collect(run_turn(**_kwargs(SlowSift())))
             if e["type"] in ("tool_call", "tool_result")]
    # as 2 chamadas aparecem no fan-out antes de qualquer resultado chegar
    assert types == ["tool_call", "tool_call", "tool_result", "tool_result"]


async def test_one_failure_does_not_kill_the_others(monkeypatch):
    fake, _ = _fake_stream([[_multi_tool_chunk(["a", "b", "c"])], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_kwargs(SlowSift(fail={"b"}))))

    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 3                      # ninguém some
    assert results[1]["result"]["error"]          # a que falhou vira erro...
    assert results[0]["result"]["id"] == "a"      # ...sem derrubar as vizinhas
    assert results[2]["result"]["id"] == "c"


async def test_single_call_still_uses_the_serial_path(monkeypatch):
    fake, _ = _fake_stream([[_multi_tool_chunk(["a"])], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_kwargs(SlowSift())))
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1 and results[0]["result"]["id"] == "a"


async def test_single_tool_failure_becomes_result_and_turn_continues(monkeypatch):
    """No caminho serial, exceção da integração não pode matar o stream."""
    fake, state = _fake_stream([[_multi_tool_chunk(["a"])], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_kwargs(SlowSift(fail={"a"}))))

    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1
    assert "boom" in results[0]["result"]["error"]
    assert [e for e in events if e["type"] == "done"][0]["content"] == "pronto"
    # O erro também volta ao modelo como resposta da tool, permitindo recuperação.
    tool_msg = next(m for m in state["messages"][1] if m.get("role") == "tool")
    assert "boom" in tool_msg["content"]


async def test_parallelism_is_capped(monkeypatch):
    """O fan-out do modelo não pode virar uma rajada contra a API de terceiros."""
    monkeypatch.setattr(orch, "_MAX_PARALLEL_TOOLS", 2)
    ids = [str(i) for i in range(4)]
    fake, _ = _fake_stream([[_multi_tool_chunk(ids)], [_final_chunk()]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)

    t0 = time.perf_counter()
    events = await _collect(run_turn(**_kwargs(SlowSift())))
    elapsed = time.perf_counter() - t0

    assert len([e for e in events if e["type"] == "tool_result"]) == 4
    # 4 tools com teto 2 => 2 ondas: mais que 1x, bem menos que 4x
    assert elapsed >= TOOL_SLEEP * 1.5, f"o teto nao foi respeitado ({elapsed:.2f}s)"
    assert elapsed < TOOL_SLEEP * 3.5
