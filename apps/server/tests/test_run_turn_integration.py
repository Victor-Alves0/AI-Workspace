"""Integração do run_turn com um stream e uma SIFT FALSOS (sem rede/DB).

Exercita o loop agêntico completo: streaming de texto/raciocínio, chamada de
ferramenta → dispatch → volta ao modelo → resposta final, o cálculo de uso, e a
REGRESSÃO do reasoning_seconds (não pode inflar com o tempo de execução da tool)."""
from __future__ import annotations

import json

import pytest

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn


# ------------------------------ fakes ----------------------------------------

class FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"

    def __init__(self, responses: dict, clock=None, tool_advance: float = 0.0):
        self._responses = responses
        self.meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}
        self._clock = clock
        self._tool_advance = tool_advance

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "web.search.query", "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        # simula o tempo de PAREDE de uma tool (p/ o teste do reasoning_seconds)
        if self._clock is not None and self._tool_advance:
            self._clock.t += self._tool_advance
        r = self._responses.get(name, {"error": "unknown"})
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _chunk(*, content=None, reasoning=None, tool=None, finish=None, usage=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    if tool is not None:
        delta["tool_calls"] = [{
            "index": 0, "id": "call_1",
            "function": {"name": tool[0], "arguments": tool[1]},
        }]
    ch = {"choices": [{"delta": delta, "finish_reason": finish}]}
    if usage is not None:
        ch["usage"] = usage
    return ch


def _fake_stream_from(scripts):
    """Monkeypatch de openrouter.stream_chat: devolve um script por invocação.
    Grava em `state` o que o "provedor" recebeu (tools anunciadas, mensagens)."""
    state = {"i": 0, "tools": [], "messages": []}

    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        state["tools"].append(tools)
        state["messages"].append(messages)
        script = scripts[min(state["i"], len(scripts) - 1)]
        state["i"] += 1
        for ch in script:
            yield ch

    return fake_stream, state


async def _collect(gen):
    return [ev async for ev in gen]


def _base_kwargs(sift):
    return dict(
        api_key="k", model="m", history=[], user_text="oi",
        chat_system_prompt=None, params={},
        session=TurnSession(user_id="u"), sift=sift, use_tools=True,
    )


# ------------------------------ testes ---------------------------------------

async def test_simple_turn_no_tools(monkeypatch):
    fake, _ = _fake_stream_from([[
        _chunk(content="Olá"), _chunk(content=" mundo", finish="stop",
                                      usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}),
    ]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_base_kwargs(FakeSift({}))))
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    done = [e for e in events if e["type"] == "done"][0]
    assert tokens == "Olá mundo"
    assert done["content"] == "Olá mundo"
    assert done["usage"]["total_tokens"] == 7


async def test_tool_loop_dispatches_and_answers(monkeypatch):
    """iteração 1: modelo chama a tool; iteração 2: responde com base no resultado."""
    scripts = [
        [_chunk(tool=("web.search.query", '{"query":"dolar"}'), finish="tool_calls")],
        [_chunk(content="O dólar está R$5", finish="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})],
    ]
    fake, _ = _fake_stream_from(scripts)
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    sift = FakeSift({"web.search.query": {"results": [{"title": "USD", "snippet": "5"}]}})
    events = await _collect(run_turn(**_base_kwargs(sift)))
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types
    call = [e for e in events if e["type"] == "tool_call"][0]
    assert call["name"] == "web.search.query"
    result = [e for e in events if e["type"] == "tool_result"][0]
    assert result["result"]["results"][0]["title"] == "USD"
    done = [e for e in events if e["type"] == "done"][0]
    assert done["content"] == "O dólar está R$5"
    # tool_events persistidos (call + result)
    kinds = [(t["kind"], t["name"]) for t in done["tool_events"]]
    assert ("call", "web.search.query") in kinds
    assert ("result", "web.search.query") in kinds


async def test_reasoning_seconds_not_inflated_by_tool_walltime(monkeypatch):
    """REGRESSÃO: pensar → tool (100s) → pensar → responder. O tempo de raciocínio
    NÃO pode incluir os 100s de execução da ferramenta."""
    clock = Clock()
    monkeypatch.setattr(orch.time, "monotonic", clock)
    scripts = [
        # iteração 1: pensa e chama a tool (SEM texto)
        [_chunk(reasoning="pensando..."),
         _chunk(tool=("web.search.query", "{}"), finish="tool_calls")],
        # iteração 2: pensa de novo e responde
        [_chunk(reasoning="quase..."), _chunk(content="resposta", finish="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})],
    ]
    fake, _ = _fake_stream_from(scripts)
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    # a tool avança o relógio em 100s (tempo de parede)
    sift = FakeSift({"web.search.query": {"ok": True}}, clock=clock, tool_advance=100.0)
    events = await _collect(run_turn(**_base_kwargs(sift)))
    done = [e for e in events if e["type"] == "done"][0]
    # com o bug, seria ~100s; com o fix, o span de cada iteração é fechado sozinho
    assert done["reasoning"]["seconds"] < 5.0


async def test_error_before_first_chunk_yields_error(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    monkeypatch.setattr(orch.openrouter, "stream_chat", boom)
    events = await _collect(run_turn(**_base_kwargs(FakeSift({}))))
    err = [e for e in events if e["type"] == "error"]
    assert err and "provider down" in err[0]["message"]


def _tool_names(tools):
    return [((t.get("function") or {}).get("name")) for t in (tools or [])]


async def test_skill_proposal_flow(monkeypatch):
    """iter 1: modelo chama propose_skill; iter 2: responde. O card vai à UI e o
    MODELO recebe só a nota 'não afirme que salvou' (proposal-only)."""
    args = json.dumps({"name": "Revisar PR", "description": "Quando revisar.",
                       "content": "# Passos", "tags": ["git"]})
    scripts = [
        [_chunk(tool=("propose_skill", args), finish="tool_calls")],
        [_chunk(content="Proposta enviada!", finish="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})],
    ]
    fake, state = _fake_stream_from(scripts)
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_base_kwargs(FakeSift({}))))
    result = [e for e in events if e["type"] == "tool_result"][0]["result"]
    assert result["kind"] == "skill_proposal" and result["slug"] == "revisar_pr"
    assert result["proposal_id"]
    # a 2ª chamada ao provedor carrega a resposta da tool: só a NOTA, sem o conteúdo
    tool_msg = [m for m in state["messages"][1] if m.get("role") == "tool"][0]
    assert "Do NOT claim the skill was saved" in tool_msg["content"]
    assert "# Passos" not in tool_msg["content"]


async def test_brain_write_flow(monkeypatch):
    """Com cérebro acoplado (write), o modelo grava uma nota: evento de progresso +
    tool_result kind:brain_note + system com o bloco '## Second brain'."""
    async def fake_write(user_id, base_id, title, content, mode):
        return {"doc_id": "d1", "base_id": base_id, "title": title,
                "action": "created", "size": len(content)}

    monkeypatch.setattr(orch.brain_service, "write_note", fake_write)
    monkeypatch.setattr(orch, "sign_doc_url", lambda did: f"/s/{did}")
    args = json.dumps({"action": "write", "title": "Decisões", "content": "corpo [[X]]"})
    scripts = [
        [_chunk(tool=("brain", args), finish="tool_calls")],
        [_chunk(content="Anotado.", finish="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6})],
    ]
    fake, state = _fake_stream_from(scripts)
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    kw = _base_kwargs(FakeSift({}))
    kw["brain"] = {"brains": ["b1"], "names": ["Notas"], "write": True, "k": 6}
    events = await _collect(run_turn(**kw))
    assert any(e.get("type") == "brain" and e.get("status") == "start" for e in events)
    result = [e for e in events if e["type"] == "tool_result"][0]["result"]
    assert result["kind"] == "brain_note" and result["title"] == "Decisões"
    # tool `brain` anunciada + bloco no system (com a fronteira p/ o mem0)
    assert "brain" in _tool_names(state["tools"][0])
    system = state["messages"][0][0]["content"]
    assert "## Second brain" in system and "memory system" in system


async def test_brain_not_injected_without_brains(monkeypatch):
    fake, state = _fake_stream_from([[_chunk(content="oi", finish="stop")]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    await _collect(run_turn(**_base_kwargs(FakeSift({}))))
    names = _tool_names(state["tools"][0])
    assert "brain" not in names
    # /learn em modo AUTO: com outras tools anunciadas (SIFT), propose_skill entra
    assert "propose_skill" in names


async def test_skill_learning_auto_skips_toolless_model(monkeypatch):
    """Modelo sem NENHUMA tool (sem SIFT/skills/KB): o auto NÃO injeta propose_skill
    (mandar `tools` p/ modelo sem tool-calling quebra o request)."""
    fake, state = _fake_stream_from([[_chunk(content="oi", finish="stop")]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    kw = _base_kwargs(None)
    kw["sift"] = None
    await _collect(run_turn(**kw))
    assert not _tool_names(state["tools"][0])
    # …mas com skill_learning=True explícito (opt-in por modelo), entra mesmo assim
    fake2, state2 = _fake_stream_from([[_chunk(content="oi", finish="stop")]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake2)
    kw2 = _base_kwargs(None)
    kw2["sift"] = None
    kw2["skill_learning"] = True
    await _collect(run_turn(**kw2))
    assert _tool_names(state2["tools"][0]) == ["propose_skill"]


async def test_usage_breakdown_present(monkeypatch):
    fake, _ = _fake_stream_from([[
        _chunk(content="oi", finish="stop",
               usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
    ]])
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    events = await _collect(run_turn(**_base_kwargs(FakeSift({}))))
    done = [e for e in events if e["type"] == "done"][0]
    assert "input_breakdown" in done["usage"]
    assert "output_breakdown" in done["usage"]
    assert done["usage"]["output_breakdown"]["output"] == 5
