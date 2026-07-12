"""Testes do _ToolDispatcher: cada handler interno + o dispatch da SIFT."""
from __future__ import annotations

import json

import pytest

from aiworkspace.chat import orchestrator as orch


class FakeSift:
    """SIFT mínima: dispatch devolve o que foi registrado por nome."""
    def __init__(self, responses: dict):
        self._responses = responses

    def dispatch(self, name, args):
        r = self._responses.get(name, {"error": "unknown tool"})
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)


def _mk(**over):
    """Dispatcher com defaults inertes; sobrescreve o que o teste precisa."""
    base = dict(
        sift=FakeSift({}), code_mode=False, api_key="k", user_id="u", chat_id="c",
        skills=[], skills_by_slug={}, genimage_on=False, genimage=None,
        kb_tool_on=False, kb_bases=[], kb_k=6, user_text="oi",
        subagents_on=False, subagents_by_key={}, subagent_max_calls=4,
        subagent_pass_context=False, subagent_worker_memory=False, run_subagent=None,
    )
    base.update(over)
    return orch._ToolDispatcher(**base)


async def _drain(disp, name, args, tc=None):
    events = []
    async for ev in disp.run(name, args, tc or {"id": "1"}):
        events.append(ev)
    return events, disp.result


async def test_view_skill_found():
    d = _mk(skills=[{"slug": "sql"}], skills_by_slug={"sql": {"slug": "sql", "name": "SQL", "content": "SELECT"}})
    _, result = await _drain(d, "view_skill", {"slug": "sql"})
    assert result["content"] == "SELECT"


async def test_view_skill_not_found_lists_known():
    d = _mk(skills=[{"slug": "sql"}], skills_by_slug={"sql": {"slug": "sql"}})
    _, result = await _drain(d, "view_skill", {"slug": "xyz"})
    assert "não encontrada" in result["error"]
    assert "sql" in result["error"]  # lista as disponíveis


async def test_run_code_gate_when_not_code_mode():
    d = _mk(code_mode=False)
    _, result = await _drain(d, "run_code", {"code": "x=1"})
    assert "não está habilitado" in result["error"]


async def test_sift_none_unavailable():
    d = _mk(sift=None)
    _, result = await _drain(d, "web.search.query", {"q": "x"})
    assert result["error"] == "ferramentas indisponíveis"


async def test_sift_dispatch_success():
    d = _mk(sift=FakeSift({"web.search.query": {"results": [{"title": "t"}]}}))
    _, result = await _drain(d, "web.search.query", {"q": "x"})
    assert json.loads(result)["results"][0]["title"] == "t"


async def test_sift_scope_error_gets_hint():
    """Path chutado → o erro ganha o hint de recuperação (usar search_tools)."""
    d = _mk(sift=FakeSift({"web.read": {"error": "web.read not allowed in this scope"}}))
    _, result = await _drain(d, "web.read", {})
    parsed = json.loads(result)
    assert "hint" in parsed
    assert "search_tools" in parsed["hint"]


async def test_search_knowledge_gate_off():
    d = _mk(kb_tool_on=False)
    _, result = await _drain(d, "search_knowledge", {"query": "x"})
    assert "não está ativa" in result["error"]


async def test_delegate_disabled():
    d = _mk(subagents_on=False)
    _, result = await _drain(d, "delegate", {"agent": "a", "task": "t"})
    assert "não habilitados" in result["error"]


async def test_delegate_limit_reached():
    d = _mk(subagents_on=True, subagent_max_calls=1,
            subagents_by_key={"a": {"name": "A"}})
    d.delegations_used = 1
    _, result = await _drain(d, "delegate", {"agent": "a", "task": "t"})
    assert "limite de 1 delegações" in result["error"]


async def test_delegate_unauthorized_agent():
    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}})
    _, result = await _drain(d, "delegate", {"agent": "desconhecido", "task": "t"})
    assert "não autorizado" in result["error"]


async def test_delegate_runs_and_counts():
    async def fake_runner(key, task):
        return {"kind": "subagent", "agent": "A", "output": f"fez: {task}"}

    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}}, run_subagent=fake_runner)
    events, result = await _drain(d, "delegate", {"agent": "a", "task": "somar"})
    assert result["output"] == "fez: somar"
    assert d.delegations_used == 1
    assert any(e.get("type") == "subagent" and e.get("status") == "start" for e in events)
    assert any(e.get("type") == "subagent" and e.get("status") == "done" for e in events)


async def test_delegate_uses_parallel_precomputed():
    """Se já rodou em paralelo (delegate_pre), reusa o resultado sem re-executar."""
    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}})
    d.delegate_pre = {"1": {"kind": "subagent", "agent": "A", "output": "pronto"}}
    events, result = await _drain(d, "delegate", {"agent": "a"}, tc={"id": "1"})
    assert result["output"] == "pronto"
    assert d.delegations_used == 0  # NÃO contou de novo


async def test_generate_image_gate_off():
    d = _mk(genimage_on=False)
    _, result = await _drain(d, "generate_image", {"prompt": "gato"})
    assert "não está ativo" in result["error"]
