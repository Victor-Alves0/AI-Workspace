"""Subagentes: agentes criados pela IA (agent="new"), worktree como interruptor geral
e compatibilidade com as configs antigas."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat import turn_setup as ts

from .test_tool_dispatcher import _drain, _mk


def _props(tool):
    return tool["function"]["parameters"]["properties"]


def test_tool_so_oferece_agente_novo_e_isolamento_quando_ligados():
    time = [{"key": "k1", "name": "Revisor", "description": "revisa PRs"}]
    so_time = orch._delegate_tool(time)
    assert _props(so_time)["agent"]["enum"] == ["k1"]
    assert "name" not in _props(so_time) and "isolated" not in _props(so_time)

    completa = orch._delegate_tool(time, adhoc=True, isolation=True)
    assert _props(completa)["agent"]["enum"] == ["k1", "new"]
    assert {"name", "instructions", "isolated"} <= set(_props(completa))
    assert "Revisor" in completa["function"]["description"]

    # sem agentes do usuário, só a criação
    assert _props(orch._delegate_tool([], adhoc=True))["agent"]["enum"] == ["new"]


def test_agente_novo_exige_nome_e_instrucoes():
    _, _, new, erro = orch._delegate_target({"agent": "new", "name": "X"}, {}, True, False)
    assert new is None and "instructions" in erro
    _, _, _, erro = orch._delegate_target({"agent": "new", "name": "X", "instructions": "y"}, {}, False, False)
    assert "não autorizado" in erro  # criação desligada

    key, label, new, erro = orch._delegate_target(
        {"agent": "new", "name": "  Pesquisador   de preços ", "instructions": "compare", "isolated": True},
        {}, True, False)
    assert (key, label, erro) == ("new", "Pesquisador de preços", None)
    assert new == {"name": "Pesquisador de preços", "instructions": "compare", "isolated": False}  # worktree off
    _, _, new, _ = orch._delegate_target(
        {"agent": "new", "name": "Dev", "instructions": "x", "isolated": True}, {}, True, True)
    assert new["isolated"] is True


async def test_dispatcher_cria_agente_e_identifica_o_evento():
    chamadas = []

    async def runner(key, task, new=None, **_kw):
        chamadas.append((key, task, new))
        return {"kind": "subagent", "agent": new["name"], "output": "ok", "adhoc": True}

    d = _mk(subagents_on=True, run_subagent=runner, subagent_adhoc=True)
    events, result = await _drain(d, "delegate", {"agent": "new", "task": "t", "name": "Dev",
                                                  "instructions": "escreva testes"}, tc={"id": "c9"})
    assert result["output"] == "ok"
    assert chamadas == [("new", "t", {"name": "Dev", "instructions": "escreva testes", "isolated": False})]
    inicio = next(e for e in events if e.get("status") == "start")
    assert inicio["id"] == "c9" and inicio["adhoc"] is True and inicio["agent"] == "Dev"
    assert inicio["mem"] is False  # agente criado não tem memória


async def test_dispatcher_devolve_erro_de_agente_novo_incompleto():
    d = _mk(subagents_on=True, run_subagent=None, subagent_adhoc=True)
    _, result = await _drain(d, "delegate", {"agent": "new", "task": "t"})
    assert "name" in result["error"] and d.delegations_used == 0


def _mc(cfg: dict, on: bool = True):
    return NS(capabilities={"subagents": on}, filter_config={"subagents": cfg})


def _resolve(cfg: dict, on: bool = True):
    return asyncio.run(ts._resolve_subagents(None, NS(id="u"), _mc(cfg, on)))


def test_config_padrao_permite_criar_agentes():
    specs, conf = _resolve({})
    assert specs == [] and conf["adhoc"] is True and conf["adhoc_model"] == ""
    assert conf["worktree"] is False and conf["isolate"] == []
    assert _resolve({"adhoc": False})[1]["adhoc"] is False
    assert _resolve({}, on=False) == ([], {})


def test_worktree_desligado_anula_o_isolamento_e_config_antiga_continua_valendo():
    # config antiga (sem `worktree`) com isolamento: continua ligado
    assert _resolve({"isolate": ["x"], "team": []})[1]["worktree"] is False  # "x" não está no time
    assert _resolve({"worktree_isolation": True})[1]["worktree"] is False     # time vazio: nada a isolar
    # interruptor explícito
    assert _resolve({"worktree": True})[1]["worktree"] is True
    assert _resolve({"worktree": False, "isolate": ["x"]})[1]["isolate"] == []


def test_opts_existem_so_com_agentes_ou_criacao():
    assert ts._subagent_opts([], {"adhoc": False}, object()) is None
    assert ts._subagent_opts([], {"adhoc": True}, None) is None
    opts = ts._subagent_opts([], {"adhoc": True, "worktree": True}, object())
    assert opts.adhoc is True and opts.isolation is True and opts.agents == []


# ------------------------- progresso, passos e segundo plano -------------------------

def test_passos_ignoram_descoberta_e_leem_o_execute_tool():
    assert ts._step_of("search_tools", {"query": "x"}) is None
    assert ts._step_of("execute_tool", {"path": "web.search.query", "params": {"query": "café  hoje"}}) \
        == ("web.search.query", "café hoje")
    assert ts._step_of("web__page__read", {"url": "https://a.b"}) == ("web.page.read", "https://a.b")


async def test_dispatcher_repassa_o_progresso_do_agente():
    async def runner(key, task, new=None, progress=None):
        progress({"tool": "web.search.query", "detail": "café"})
        progress({"tool": "web.page.read", "detail": "https://x"})
        return {"kind": "subagent", "agent": "A", "output": "ok", "steps": []}

    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}}, run_subagent=runner)
    events, result = await _drain(d, "delegate", {"agent": "a", "task": "t"}, tc={"id": "c1"})
    prog = [e for e in events if e.get("status") == "progress"]
    assert [(e["id"], e["tool"]) for e in prog] == [("c1", "web.search.query"), ("c1", "web.page.read")]
    assert result["output"] == "ok"


async def test_segundo_plano_solta_o_agente_e_responde_na_hora():
    soltos = []

    async def runner(*_a, **_kw):
        raise AssertionError("não deveria rodar inline")

    runner.start_background = lambda key, task, new, label: soltos.append((key, task, label)) or "job1"
    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}}, run_subagent=runner,
            subagent_background=True)
    events, result = await _drain(d, "delegate", {"agent": "a", "task": "longa", "background": True})
    assert soltos == [("a", "longa", "A")]
    assert result["kind"] == "subagent_started" and result["job_id"] == "job1"
    assert [e["status"] for e in events if e.get("type") == "subagent"] == ["background"]


async def test_sem_permissao_de_segundo_plano_roda_inline():
    async def runner(key, task, **_kw):
        return {"kind": "subagent", "agent": "A", "output": "inline"}

    runner.start_background = lambda *a: "nunca"
    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}}, run_subagent=runner)
    _, result = await _drain(d, "delegate", {"agent": "a", "task": "t", "background": True})
    assert result["output"] == "inline"


def test_modelo_nao_recebe_os_passos():
    import json

    content, ui = orch._shape_tool_result({"kind": "subagent", "agent": "A", "task": "t", "output": "r",
                                           "steps": [{"tool": "x"}],
                                           "timeline": [{"kind": "reasoning", "text": "pensando"}]})
    visto = json.loads(content)
    assert "steps" not in visto and "task" not in visto and "timeline" not in visto
    assert ui["steps"] == [{"tool": "x"}]
    assert ui["timeline"] == [{"kind": "reasoning", "text": "pensando"}]


async def test_agentes_que_terminam_juntos_viram_um_wake_so(monkeypatch):
    from aiworkspace.chat import generation, resume, subagent_jobs as sj

    wakes = []

    async def fake_resume(chat_id, text, **kw):
        wakes.append((chat_id, text, kw["notify_title"]))

    ativo = {"on": True}
    monkeypatch.setattr(generation, "get_active",
                        lambda cid: type("G", (), {"done": not ativo["on"]})() if ativo["on"] else None)
    monkeypatch.setattr(resume, "resume_chat_turn", fake_resume)
    dormir = asyncio.sleep
    monkeypatch.setattr(sj.asyncio, "sleep", lambda _s: dormir(0))

    async def rel(txt):
        return {"kind": "subagent", "agent": "x", "output": txt}

    sj.start("chat1", "Café", "preço do café", lambda: rel("R1"))
    sj.start("chat1", "Chá", "preço do chá", lambda: rel("R2"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert wakes == []            # o chat ainda está gerando: espera
    ativo["on"] = False
    for _ in range(50):
        await asyncio.sleep(0)
    assert len(wakes) == 1
    chat, texto, titulo = wakes[0]
    assert chat == "chat1" and "R1" in texto and "R2" in texto and titulo.startswith("2 agentes")
    assert texto.startswith(sj.NOTE_PREFIX)


def test_nota_de_falha_e_relatorio_longo():
    from aiworkspace.chat import subagent_jobs as sj

    assert "Falhou: boom" in sj.note_for("A", "t", {"error": "boom"})
    longo = sj.note_for("A", "t", {"output": "x" * 20000})
    assert "[relatório cortado]" in longo and len(longo) < 13000
