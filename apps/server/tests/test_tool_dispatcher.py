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
        brain_on=False, brain_write=False, brain_ids=[], brain_names=[], brain_k=6,
        skill_learning_on=True,
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


# ------------------------------ second brain ---------------------------------

async def test_brain_gate_off():
    d = _mk(brain_on=False)
    _, result = await _drain(d, "brain", {"action": "list"})
    assert "not enabled" in result["error"]


async def test_brain_unknown_action_lists_valid():
    d = _mk(brain_on=True, brain_write=True, brain_ids=["b1"])
    _, result = await _drain(d, "brain", {"action": "explodir"})
    assert "list, search, read, write" in result["error"]
    # sem escrita, write não é oferecido no erro
    d2 = _mk(brain_on=True, brain_write=False, brain_ids=["b1"])
    _, r2 = await _drain(d2, "brain", {"action": "explodir"})
    assert "write" not in r2["error"]


async def test_brain_write_gate_off():
    d = _mk(brain_on=True, brain_write=False, brain_ids=["b1"])
    _, result = await _drain(d, "brain", {"action": "write", "title": "N", "content": "x"})
    assert "disabled" in result["error"]


async def test_brain_write_emits_event_and_kind(monkeypatch):
    async def fake_write(user_id, base_id, title, content, mode):
        assert base_id == "b1" and mode == "replace"
        return {"doc_id": "d1", "base_id": base_id, "title": title, "action": "created", "size": 9}

    monkeypatch.setattr(orch.brain_service, "write_note", fake_write)
    monkeypatch.setattr(orch, "sign_doc_url", lambda did: f"/signed/{did}")
    d = _mk(brain_on=True, brain_write=True, brain_ids=["b1"], brain_names=["Notas"])
    events, result = await _drain(d, "brain", {"action": "write", "title": "Nota X", "content": "corpo [[Y]]"})
    assert any(e.get("type") == "brain" and e.get("status") == "start" for e in events)
    assert result["kind"] == "brain_note" and result["action"] == "created"
    assert result["url"] == "/signed/d1" and result["preview"].startswith("corpo")


async def test_brain_write_picks_brain_by_name(monkeypatch):
    seen = {}

    async def fake_write(user_id, base_id, title, content, mode):
        seen["base"] = base_id
        return {"doc_id": "d", "base_id": base_id, "title": title, "action": "updated", "size": 1}

    monkeypatch.setattr(orch.brain_service, "write_note", fake_write)
    monkeypatch.setattr(orch, "sign_doc_url", lambda did: "u")
    d = _mk(brain_on=True, brain_write=True, brain_ids=["b1", "b2"],
            brain_names=["Pessoal", "Projetos"])
    await _drain(d, "brain", {"action": "write", "title": "N", "content": "c", "brain": "projetos"})
    assert seen["base"] == "b2"


async def test_brain_search_reuses_knowledge_shape(monkeypatch):
    async def fake_search(user_id, base_ids, query, k):
        assert base_ids == ["b1"] and k == 3
        return [{"chunk_id": "c", "doc_id": "d1", "filename": "N.md", "ordinal": 0,
                 "text": "trecho", "score": 0.9}]

    monkeypatch.setattr(orch.kb_retrieval, "search", fake_search)
    monkeypatch.setattr(orch, "sign_doc_url", lambda did: f"/s/{did}")
    d = _mk(brain_on=True, brain_ids=["b1"], brain_k=3)
    events, result = await _drain(d, "brain", {"action": "search", "query": "x"})
    assert result["kind"] == "knowledge" and result["count"] == 1
    assert "[1] trecho" in result["_model"]
    assert any(e.get("type") == "knowledge" for e in events)


async def test_brain_read_with_links_and_backlinks(monkeypatch):
    docs = [
        {"id": "1", "base_id": "b1", "filename": "Alvo.md", "title": "Alvo",
         "text": "conteúdo com [[Outra]]", "updated_at": None},
        {"id": "2", "base_id": "b1", "filename": "Citador.md", "title": "Citador",
         "text": "menciona [[Alvo]]", "updated_at": None},
    ]

    async def fake_load(base_ids, user_id):
        return docs

    monkeypatch.setattr(orch.brain_service, "load_brain_docs", fake_load)
    d = _mk(brain_on=True, brain_ids=["b1"])
    _, result = await _drain(d, "brain", {"action": "read", "title": "alvo"})
    assert result["title"] == "Alvo" and result["links"] == ["Outra"]
    assert result["backlinks"] == ["Citador"]


async def test_brain_read_not_found_lists_titles(monkeypatch):
    async def fake_load(base_ids, user_id):
        return [{"id": "1", "base_id": "b", "filename": "A.md", "title": "A",
                 "text": "", "updated_at": None}]

    monkeypatch.setattr(orch.brain_service, "load_brain_docs", fake_load)
    d = _mk(brain_on=True, brain_ids=["b1"])
    _, result = await _drain(d, "brain", {"action": "read", "title": "Zeta"})
    assert "not found" in result["error"] and "A" in result["error"]


# -------------------------------- /learn -------------------------------------

async def test_propose_skill_returns_proposal_without_saving():
    d = _mk(skill_learning_on=True)
    _, result = await _drain(d, "propose_skill", {
        "name": "Revisão de PR", "description": "Quando revisar PRs.",
        "content": "# Passos\n1. ...", "tags": ["git", "git", "review"],
    })
    assert result["kind"] == "skill_proposal"
    assert result["proposal_id"]
    assert result["slug"] == "revis_o_de_pr"  # slug normalizado do nome
    assert result["tags"] == ["git", "review"]  # dedup


async def test_propose_skill_slug_normalized_from_arg():
    d = _mk(skill_learning_on=True)
    _, result = await _drain(d, "propose_skill", {
        "name": "X", "slug": "Code Review!", "description": "d", "content": "c",
    })
    assert result["slug"] == "code_review"


async def test_propose_skill_requires_fields():
    d = _mk(skill_learning_on=True)
    _, result = await _drain(d, "propose_skill", {"name": "X", "description": "", "content": "c"})
    assert "required" in result["error"]


async def test_propose_skill_gate_off():
    d = _mk(skill_learning_on=False)
    _, result = await _drain(d, "propose_skill", {"name": "X", "description": "d", "content": "c"})
    assert "not enabled" in result["error"]
