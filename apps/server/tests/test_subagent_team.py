"""Equipes de subagentes (`delegate_team`): teto do turno, fila de concorrência por nível,
síntese em camadas e o que o orquestrador enxerga."""
from __future__ import annotations

import asyncio
import json

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat import subagent_team as team

from .test_tool_dispatcher import _drain, _mk


def test_membros_normalizados_com_instrucoes_compartilhadas():
    membros, erro = team.team_members({
        "shared_instructions": "Responda em tópicos.",
        "members": [
            {"name": "  SEO   ", "task": "palavras-chave", "instructions": "foco em Google"},
            {"task": "anúncios"},
            {"name": "sem tarefa"},
            "lixo",
        ],
    })
    assert erro is None and len(membros) == 2
    assert membros[0]["name"] == "SEO"
    assert membros[0]["instructions"] == "Responda em tópicos.\n\nfoco em Google"
    assert membros[1]["name"] == "Agente 2" and membros[1]["instructions"] == "Responda em tópicos."
    assert team.team_members({"members": []})[1]
    assert team.team_members({"members": [{"name": "x"}]})[1]


async def test_pool_limita_o_turno_e_a_concorrencia_por_nivel():
    pool = team.SubagentPool(limit=3, concurrency=2)
    assert [pool.take() for _ in range(4)] == [True, True, True, False]
    assert team.SubagentPool(limit=5000, concurrency=500).limit == team.MAX_AGENTS

    ativos, pico = 0, 0

    async def trabalho():
        nonlocal ativos, pico
        async with pool.slot(0):
            ativos += 1
            pico = max(pico, ativos)
            await asyncio.sleep(0.01)
            ativos -= 1

    await asyncio.gather(*[trabalho() for _ in range(10)])
    assert pico == 2
    # outro nível tem vagas próprias: o líder esperando a equipe nunca trava a fila
    assert pool.slot(1) is not pool.slot(0)


async def test_equipe_roda_todos_e_junta_os_relatorios():
    eventos = []

    async def run(key, task, new=None, progress=None):
        progress({"state": "running"})
        progress({"tool": "web.search", "detail": task})
        if task == "falha":
            return {"error": "boom"}
        return {"kind": "subagent", "agent": new["name"], "output": f"relatório de {task}",
                "timeline": [{"kind": "reasoning", "text": "x" * 50}]}

    membros = [{"name": f"A{i}", "task": t, "instructions": "i", "isolated": False}
               for i, t in enumerate(["café", "chá", "falha"])]
    res = await team.run_team(run, membros, "comparar bebidas", eventos.append, None)
    assert res["kind"] == "subagent_team" and res["size"] == 3
    assert (res["succeeded"], res["failed"]) == (2, 1)
    assert "relatório de café" in res["report"] and "FAILED: boom" in res["report"]
    assert res["members"][2]["error"] == "boom"
    assert {e["status"] for e in eventos} == {"running", "progress", "done"}
    assert [e["ok"] for e in eventos if e["status"] == "done"].count(False) == 1


async def test_relatorios_grandes_viram_sintese_em_camadas():
    chamadas = []

    async def run(key, task, new=None, progress=None):
        return {"output": "z" * 9000}

    async def sintese(goal, texto):
        chamadas.append(len(texto))
        return "resumo curto"

    membros = [{"name": f"A{i}", "task": "t", "instructions": "", "isolated": False} for i in range(20)]
    eventos = []
    res = await team.run_team(run, membros, "meta", eventos.append, sintese)
    assert res["synthesized"] is True and "resumo curto" in res["report"]
    assert len(res["report"]) < team._REPORT_BUDGET
    assert chamadas and all(n <= team._SYNTH_CHUNK + 2000 for n in chamadas)
    assert any(e.get("status") == "synthesis" for e in eventos)


def test_modelo_ve_o_relatorio_e_nao_as_linhas_do_tempo():
    res = {"kind": "subagent_team", "goal": "g", "size": 2, "succeeded": 2, "failed": 0,
           "report": "R", "synthesized": False,
           "members": [{"agent": "A", "output": "o", "timeline": [{"kind": "text", "text": "t"}]},
                       {"agent": "B", "output": "o", "task_id": "w1"}]}
    content, ui = orch._shape_tool_result(res)
    visto = json.loads(content)
    assert visto["report"] == "R" and "timeline" not in content
    assert visto["members"] == [{"agent": "A", "ok": True}, {"agent": "B", "ok": True, "task_id": "w1"}]
    assert "worktree" in visto["note"]
    assert ui["members"][0]["timeline"]  # a UI recebe tudo
    grande = {**res, "members": [{"agent": f"A{i}"} for i in range(100)]}
    assert "members" not in team.model_view(grande)


def test_tool_de_equipe_so_com_criacao_de_agentes():
    tool = orch._delegate_team_tool(50, isolation=True, background=True)
    props = tool["function"]["parameters"]["properties"]
    assert props["members"]["maxItems"] == 50
    assert "isolated" in props["members"]["items"]["properties"] and "background" in props
    assert "isolated" not in orch._delegate_team_tool(5)["function"]["parameters"]["properties"]["members"]["items"]["properties"]


async def test_dispatcher_recusa_equipe_acima_do_teto():
    async def run(key, task, new=None, progress=None):
        return {"output": "ok"}

    d = _mk(subagents_on=True, run_subagent=run, subagent_adhoc=True, subagent_max_calls=2)
    _, result = await _drain(d, "delegate_team", {
        "team_name": "T", "goal": "g",
        "members": [{"name": f"A{i}", "task": "t"} for i in range(3)]}, tc={"id": "t1"})
    assert "restam 2" in result["error"]


async def test_dispatcher_roda_a_equipe_e_marca_os_eventos_por_membro():
    async def run(key, task, new=None, progress=None):
        progress({"state": "running"})
        return {"kind": "subagent", "agent": new["name"], "output": f"ok {task}"}

    d = _mk(subagents_on=True, run_subagent=run, subagent_adhoc=True, subagent_max_calls=10)
    events, result = await _drain(d, "delegate_team", {
        "team_name": "Marketing", "goal": "plano",
        "members": [{"name": "SEO", "task": "a"}, {"name": "Ads", "task": "b"}]}, tc={"id": "t2"})
    assert result["kind"] == "subagent_team" and result["team"] == "Marketing"
    assert result["succeeded"] == 2 and d.delegations_used == 2
    inicio = next(e for e in events if e.get("status") == "team_start")
    assert inicio["id"] == "t2" and [m["name"] for m in inicio["members"]] == ["SEO", "Ads"]
    membros = [e for e in events if e.get("status") == "done"]
    assert sorted(e["member"] for e in membros) == [0, 1] and all(e["team"] == "Marketing" for e in membros)
    assert events[-1]["status"] == "team_done"


async def test_em_cadeia_roda_em_ordem_e_cada_um_ve_os_anteriores():
    ativos, pico, vistos = 0, 0, []

    async def run(key, task, new=None, progress=None):
        nonlocal ativos, pico
        ativos += 1
        pico = max(pico, ativos)
        vistos.append(task)
        await asyncio.sleep(0.01)
        ativos -= 1
        return {"output": f"saída de {new['name']}"}

    membros = [{"name": n, "task": f"tarefa {n}", "instructions": "", "isolated": False}
               for n in ("Pesquisa", "Estratégia", "Texto")]
    res = await team.run_team(run, membros, "g", lambda ev: None, None, chain=True)
    assert pico == 1 and res["chained"] is True
    assert vistos[0] == "tarefa Pesquisa"
    assert "saída de Pesquisa" in vistos[1] and "saída de Estratégia" not in vistos[1]
    assert "saída de Pesquisa" in vistos[2] and "saída de Estratégia" in vistos[2]
    assert res["members"][1]["task"] == "tarefa Estratégia"  # a UI mostra a tarefa limpa


async def test_ia_escolhe_cadeia_com_parallel_false():
    ordem = []

    async def run(key, task, new=None, progress=None):
        ordem.append(new["name"])
        await asyncio.sleep(0.01 if new["name"] == "A" else 0)
        return {"output": "ok"}

    d = _mk(subagents_on=True, run_subagent=run, subagent_adhoc=True, subagent_max_calls=10)
    events, result = await _drain(d, "delegate_team", {
        "team_name": "T", "goal": "g", "parallel": False,
        "members": [{"name": "A", "task": "a"}, {"name": "B", "task": "b"}]}, tc={"id": "t3"})
    assert result["chained"] is True and ordem == ["A", "B"]
    assert next(e for e in events if e.get("status") == "team_start")["chain"] is True
    assert "parallel" in orch._delegate_team_tool(5)["function"]["parameters"]["properties"]


async def test_padrao_e_paralelo_e_o_mode_antigo_e_ignorado():
    from types import SimpleNamespace as NS

    from aiworkspace.chat import turn_setup as ts

    async def conf(sub):
        mc = NS(capabilities={"subagents": True}, filter_config={"subagents": sub})
        return (await ts._resolve_subagents(None, NS(id=1), mc))[1]

    assert (await conf({}))["mode"] == "parallel"
    # o editor antigo gravava mode="sequential" em todo modelo: não conta como escolha
    assert (await conf({"mode": "sequential"}))["mode"] == "parallel"
    assert (await conf({"execution": "sequential"}))["mode"] == "sequential"
    assert (await conf({"max_calls": 5000, "concurrency": 999}))["max_calls"] == team.MAX_AGENTS
    assert (await conf({"concurrency": 999}))["concurrency"] == team.MAX_CONCURRENCY
    assert ts._subagent_opts([], {"adhoc": True}, lambda *a, **k: None).mode == "parallel"


def test_passo_leva_so_o_resumo_dos_argumentos():
    from aiworkspace.chat import turn_setup as ts

    r = ts._step_args("code__files__write", {
        "action": "patch", "message": "x" * 500,
        "diff": "--- a/README.md\n+++ b/README.md\n@@\n--- a/x.py\n+++ b/x.py\n", "content": "segredo"})
    assert r == {"action": "patch", "files": ["README.md", "x.py"]}
    assert ts._step_args("execute_tool", {"path": "web.search.query", "params": {"query": "  café   hoje "}}) \
        == {"query": "café hoje"}
    assert ts._step_args("delegate_team", {"team_name": "M", "members": [{}, {}]}) == {"team_name": "M", "members": 2}
    assert len(ts._step_args("x", {"query": "y" * 500})["query"]) == 120
