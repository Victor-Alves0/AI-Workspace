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

    async def runner(key, task, new=None):
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
