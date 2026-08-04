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


def test_is_selfbound_wait_reconhece_as_formas():
    f = orch._is_selfbound_wait
    # tool fixada, ponto e __; via execute_tool
    assert f(("code.exec.jobs", {"action": "wait"}))
    assert f(("code__exec__jobs", {"action": "wait"}))
    assert f(("code.preview.serve", {"action": "wait"}))
    assert f(("execute_tool", {"path": "code.exec.jobs", "params": {"action": "wait"}}))
    # NÃO isenta: outras ações da mesma tool, ou outras tools
    assert not f(("code.exec.jobs", {"action": "list"}))
    assert not f(("code.preview.serve", {"action": "status"}))
    assert not f(("code.flow.analyze", {"action": "taint"}))
    assert not f(("execute_tool", {"path": "code.flow.analyze", "params": {"action": "wait"}}))
    # robustez: formas estranhas não podem CRASHAR (devolvem False com segurança)
    assert f(("code.exec.jobs", {"action": " WAIT "}))          # espaço + maiúscula
    assert not f(())                                             # call vazio
    assert not f(("code.exec.jobs", ["action", "wait"]))        # args não-dict


def test_selfbound_wait_com_params_string_json():
    """Se o dispatch passar `params` como STRING JSON (em vez de dict) via execute_tool, o
    wait ainda tem que ser reconhecido como auto-limitado (senão o watchdog o mataria).
    _is_selfbound_wait parseia o JSON. Entrada estranha (JSON inválido) → não isenta, sem crash."""
    assert orch._is_selfbound_wait(
        ("execute_tool", {"path": "code.exec.jobs", "params": '{"action":"wait"}'}))
    # JSON inválido / tipos estranhos não podem crashar nem isentar indevidamente
    assert not orch._is_selfbound_wait(
        ("execute_tool", {"path": "code.exec.jobs", "params": "{lixo"}))
    assert not orch._is_selfbound_wait(
        ("execute_tool", {"path": "code.exec.jobs", "params": 123}))


class _WaitSift:
    """dispatch dorme > watchdog; grava a thread onde rodou (p/ checar o executor)."""
    def __init__(self):
        self.threads: dict = {}
    def dispatch(self, name, args):
        import threading
        self.threads[args.get("action")] = threading.current_thread().name
        time.sleep(2.0)
        return '{"status":"running","note":"ainda subindo"}'


def test_selfbound_wait_nao_e_morto_pelo_watchdog():
    """code.exec.jobs/preview.serve action=wait BLOQUEIA de propósito (teto próprio) —
    o watchdog de 120s NÃO pode abortá-lo (bug do Metabase: 6 waits mortos aos 120s)."""
    d = orch._ToolDispatcher.__new__(orch._ToolDispatcher)
    d.sift = _WaitSift()
    s = orch.get_settings()
    old = s.builtin_tool_timeout_seconds
    s.builtin_tool_timeout_seconds = 1  # teto curto; o dispatch dorme 2s
    try:
        async def go():
            # wait → ISENTO: espera os 2s e devolve o resultado real (não o erro de abort)
            waited = await d._dispatch_tp("code.exec.jobs", {"action": "wait"})
            # list → NÃO isento: é abortado pelo watchdog aos ~1s
            listed = await d._dispatch_tp("code.exec.jobs", {"action": "list"})
            return waited, listed
        waited, listed = asyncio.run(go())
    finally:
        s.builtin_tool_timeout_seconds = old
    assert "ainda subindo" in str(waited) and "abortada" not in str(waited), waited
    assert "abortada" in str(listed), listed
    # o wait rodou no executor DEDICADO (não esfomeia o dispatch das demais tools)
    assert d.sift.threads.get("wait", "").startswith("selfbound-wait"), d.sift.threads


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
