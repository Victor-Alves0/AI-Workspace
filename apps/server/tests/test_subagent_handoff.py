"""Entrega ao subagente: tools concedidas pelo orquestrador (nunca além das dele), dados
e anexos entregues junto da tarefa, e o pedido do agente ao orquestrador
(`request_from_lead` → `needs_input` → `continue_agent`)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat import subagent_team as team
from aiworkspace.chat import turn_setup as ts
from aiworkspace.chat.orchestrator import NativeToolOpts, TurnSession, run_turn
from aiworkspace.tools import loader

from .test_tool_dispatcher import _drain, _mk

# ------------------------------ concessão de tools ------------------------------


def test_concessao_nunca_amplia_o_escopo_do_orquestrador():
    allow = ["code.files.browse", "web.page.read", "web.search.query"]
    estreito, aceitos, recusados = loader.narrow_allow(
        allow, ["web.search.query", "code.exec.run", "gmail.mail.send"])
    # a busca leva junto a leitura da página (companion) — que o orquestrador tem
    assert estreito == ["web.page.read", "web.search.query"]
    assert aceitos == ["web.search.query"]
    assert recusados == ["code.exec.run", "gmail.mail.send"]
    # grupo concedido estreita para o que existe dentro dele
    assert loader.narrow_allow(allow, ["web.*"])[0] == ["web.page.read", "web.search.query"]
    # o orquestrador tem um curinga mais largo: o path específico entra
    assert loader.narrow_allow(["finance.*"], ["finance.stocks.quote"])[1] == ["finance.stocks.quote"]
    assert loader.narrow_allow(["finance.*"], ["finance.stocks.*"])[0] == ["finance.stocks.*"]
    # nada do orquestrador: escopo vazio
    assert loader.narrow_allow(allow, ["shell.root.run"])[0] == []


def test_nomes_de_tools_concedidas_sao_normalizados():
    assert loader.normalize_grant(None) is None                     # não pediu escopo
    assert loader.normalize_grant([]) == []                         # sem tools
    assert loader.normalize_grant(["web__search__query", "builtin:web.page.read", "web.search.query",
                                   "  web  ", "code.files"]) == [
        "web.search.query", "web.page.read", "web.*", "code.files.*"]
    assert loader.normalize_grant("web.search.query") == ["web.search.query"]


async def test_loader_aplica_a_concessao_no_escopo_real(monkeypatch):
    """get_sift_for_user(grant=...) monta o escopo com a interseção — e diz o que recusou."""
    vistos = {}

    class Full:
        def scope(self, allow, pin=None):
            vistos["allow"], vistos["pin"] = allow, pin
            return NS(meta={}, openai_tools=list)

    class Db:
        async def scalars(self, _q):
            return []

    async def configs(*_a, **_k):
        return (None,) * 16

    monkeypatch.setattr(loader, "_assemble_configs", configs)
    monkeypatch.setattr(loader.sift_service, "get_user_sift", lambda *a: Full())
    mc = NS(tools_enabled=True, tool_ids=["builtin:web.search.query", "builtin:finance.stocks.quote"],
            sift_config={}, code_mode=False)
    scope = await loader.get_sift_for_user(Db(), "u", mc, grant=["web.search.query", "code.exec.run"])
    assert vistos["allow"] == ["web.page.read", "web.search.query"]
    assert all(p in vistos["allow"] for p in (vistos["pin"] or []))  # pin fora do allow derrubaria tudo
    assert scope.meta["grant"] == {"granted": ["web.search.query"], "not_granted": ["code.exec.run"]}
    assert scope.meta["tool_paths"] == vistos["allow"]
    # sem concessão: o escopo inteiro do orquestrador (comportamento de sempre)
    scope = await loader.get_sift_for_user(Db(), "u", mc)
    assert "finance.stocks.quote" in vistos["allow"] and "grant" not in scope.meta
    assert "finance.stocks.quote" in scope.meta["tool_paths"]


def test_tool_delegate_oferece_concessao_contexto_anexos_e_retomada():
    t = orch._delegate_tool([], adhoc=True, grantable=["web.search.query", "code.files.browse"])
    props = t["function"]["parameters"]["properties"]
    assert {"tools", "context", "attachments", "continue_agent"} <= set(props)
    assert t["function"]["parameters"]["required"] == ["task"]
    desc = t["function"]["description"]
    assert "web.search.query, code.files.browse" in desc and "needs_input" in desc
    membro = orch._delegate_team_tool(5)["function"]["parameters"]
    assert {"tools", "context"} <= set(membro["properties"]["members"]["items"]["properties"])
    assert {"context", "attachments"} <= set(membro["properties"])
    # agentes do usuário (sem criação): não há concessão, mas há entrega e retomada
    so_time = orch._delegate_tool([{"key": "k", "name": "K"}])["function"]["parameters"]["properties"]
    assert "tools" not in so_time and {"context", "continue_agent"} <= set(so_time)


def test_agente_novo_leva_a_concessao_so_quando_pedida():
    _, _, new, _ = orch._delegate_target({"agent": "new", "name": "A", "instructions": "i"}, {}, True, False)
    assert "tools" not in new
    _, _, new, _ = orch._delegate_target({"agent": "new", "name": "A", "instructions": "i",
                                          "tools": ["web__search__query"]}, {}, True, False)
    assert new["tools"] == ["web.search.query"]


# ------------------------------ contexto e anexos ------------------------------

async def test_contexto_e_anexos_chegam_ao_agente():
    chamadas = []

    async def runner(key, task, new=None, progress=None, **kw):
        chamadas.append((key, task, new, kw))
        return {"kind": "subagent", "agent": new["name"], "task": task, "output": "feito",
                "tools_granted": ["web.search.query"], "tools_not_granted": ["code.exec.run"]}

    anexos = [{"type": "image", "name": "print.png", "url": "data:image/png;base64,AAA"},
              {"type": "file", "name": "notas.txt", "text": "x" * (orch._HANDOFF_FILE_CAP + 10)}]
    d = _mk(subagents_on=True, run_subagent=runner, subagent_adhoc=True, attachments=anexos)
    _, res = await _drain(d, "delegate", {
        "agent": "new", "name": "Analista", "instructions": "analise", "task": "resuma",
        "tools": ["web.search.query", "code.exec.run"], "context": "orçamento: R$ 500",
        "attachments": ["print.png", "notas.txt"]}, tc={"id": "c1"})
    _key, _task, new, kw = chamadas[0]
    assert new["tools"] == ["web.search.query", "code.exec.run"]  # a interseção é no runner
    ho = kw["handoff"]
    assert ho["context"] == "orçamento: R$ 500"
    assert [a["name"] for a in ho["attachments"]] == ["print.png", "notas.txt"]
    arq = ho["attachments"][1]
    assert arq["truncated"] and "read_attachment" in arq["text"]  # arquivo grande: começo + como ler o resto
    assert res["agent_ref"] == "c1" and res["context"] == "orçamento: R$ 500"
    visto = json.loads(orch._shape_tool_result(res)[0])
    assert "context" not in visto and visto["tools_not_granted"] == ["code.exec.run"]

    # attachments=true entrega todos; nome desconhecido volta como aviso
    chamadas.clear()
    d2 = _mk(subagents_on=True, run_subagent=runner, subagent_adhoc=True, attachments=anexos, chat_id=None)
    _, res = await _drain(d2, "delegate", {"agent": "new", "name": "A", "instructions": "i",
                                           "task": "t", "attachments": True}, tc={"id": "c2"})
    assert len(chamadas[0][3]["handoff"]["attachments"]) == 2
    _, res = await _drain(d2, "delegate", {"agent": "new", "name": "A", "instructions": "i",
                                           "task": "t", "attachments": ["nao_existe.pdf"]}, tc={"id": "c3"})
    assert res["attachments_not_found"] == ["nao_existe.pdf"]


async def test_delegacao_antiga_continua_igual():
    """Sem contexto/anexos/concessão/retomada o runner é chamado exatamente como antes."""
    recebidos = []

    async def runner(key, task, new=None, progress=None):  # assinatura antiga, sem **kw
        recebidos.append((key, task, new))
        return {"kind": "subagent", "agent": "A", "output": "ok"}

    d = _mk(subagents_on=True, subagents_by_key={"a": {"name": "A"}}, run_subagent=runner,
            subagent_adhoc=True)
    _, res = await _drain(d, "delegate", {"agent": "a", "task": "t"}, tc={"id": "c1"})
    _, res2 = await _drain(d, "delegate", {"agent": "new", "name": "N", "instructions": "i", "task": "u"},
                           tc={"id": "c2"})
    assert recebidos == [("a", "t", None), ("new", "u", {"name": "N", "instructions": "i", "isolated": False})]
    assert res["output"] == "ok" and res["agent_ref"] == "c1" and res2["agent_ref"] == "c2"


async def test_equipe_recebe_contexto_comum_individual_e_concessao():
    vistos = []

    async def run(key, task, new=None, progress=None, **kw):
        vistos.append((new["name"], new.get("tools"), kw.get("handoff")))
        return {"output": f"relatório {new['name']}"}

    membros, erro = team.team_members({"members": [
        {"name": "A", "task": "t1", "tools": ["web.search.query"], "context": "só do A"},
        {"name": "B", "task": "t2"}]})
    assert erro is None and membros[0]["tools"] == ["web.search.query"] and "tools" not in membros[1]
    d = _mk(subagents_on=True, run_subagent=run, subagent_adhoc=True, subagent_max_calls=10,
            attachments=[{"type": "file", "name": "a.csv", "text": "1,2"}])
    _, res = await _drain(d, "delegate_team", {
        "team_name": "T", "goal": "g", "context": "comum", "attachments": True,
        "members": [{"name": "A", "task": "t1", "tools": ["web.search.query"], "context": "só do A"},
                    {"name": "B", "task": "t2"}]}, tc={"id": "eq"})
    por_nome = {n: (t, h) for n, t, h in vistos}
    assert por_nome["A"][0] == ["web.search.query"] and por_nome["B"][0] is None
    assert por_nome["A"][1]["context"] == "comum\n\nsó do A" and por_nome["B"][1]["context"] == "comum"
    assert por_nome["B"][1]["attachments"][0]["name"] == "a.csv"
    assert [m["ref"] for m in res["members"]] == ["eq#0", "eq#1"]
    assert "eq#1" in d.agent_sessions


# ------------------------------ pedido ao orquestrador ------------------------------

async def test_agente_pede_e_orquestrador_retoma_o_mesmo_agente():
    chamadas = []

    async def runner(key, task, new=None, progress=None, **kw):
        chamadas.append((key, task, new, kw))
        if len(chamadas) == 1:
            return {"kind": "subagent", "agent": new["name"], "task": task,
                    "output": "Achei 3 fornecedores; falta o preço de cada.",
                    "status": "needs_input", "needs": {"kind": "tool", "need": "web.page.read p/ ler os sites"},
                    "tools_granted": ["web.search.query"]}
        return {"kind": "subagent", "agent": new["name"], "task": task, "output": "Preços: 10, 12, 15."}

    d = _mk(subagents_on=True, run_subagent=runner, subagent_adhoc=True)
    _, res = await _drain(d, "delegate", {"agent": "new", "name": "Compras", "instructions": "cote",
                                          "task": "cote cadeiras", "tools": ["web.search.query"],
                                          "context": "até R$ 20"}, tc={"id": "c1"})
    assert res["status"] == "needs_input" and res["agent_ref"] == "c1"
    assert 'continue_agent="c1"' in res["how_to_answer"] and "tools=" in res["how_to_answer"]
    visto = json.loads(orch._shape_tool_result(res)[0])
    assert visto["needs"]["kind"] == "tool" and visto["status"] == "needs_input"

    _, res2 = await _drain(d, "delegate", {"continue_agent": "c1", "task": "liberei a leitura de páginas",
                                           "tools": ["web.page.read"]}, tc={"id": "c2"})
    key, task, new, kw = chamadas[1]
    assert key == "new" and task == "liberei a leitura de páginas"
    assert new["name"] == "Compras" and new["instructions"] == "cote"
    assert new["tools"] == ["web.page.read", "web.search.query"]  # só acrescenta o pedido
    hist = kw["prior"]
    assert hist[0] == {"role": "user", "content": "cote cadeiras\n\n## Context from the lead\naté R$ 20"}
    assert "falta o preço" in hist[1]["content"] and "web.page.read p/ ler os sites" in hist[1]["content"]
    assert kw["handoff"]["ask_lead"] is True
    assert res2["output"] == "Preços: 10, 12, 15." and res2["continues"] == "c1"
    assert d.delegations_used == 2
    # retomar a retomada leva a conversa inteira
    await _drain(d, "delegate", {"continue_agent": "c2", "task": "e o frete?"}, tc={"id": "c3"})
    assert len(chamadas[2][3]["prior"]) == 4


async def test_retomada_de_agente_desconhecido_explica_o_erro():
    d = _mk(subagents_on=True, run_subagent=lambda *a, **k: None, subagent_adhoc=True, chat_id=None)
    _, res = await _drain(d, "delegate", {"continue_agent": "call_x", "task": "oi"})
    assert "não encontrado" in res["error"] and d.delegations_used == 0


def test_equipe_mostra_quem_pediu_algo_e_como_responder():
    async def run(key, task, new=None, progress=None, **kw):
        if new["name"] == "B":
            return {"output": "parcial", "status": "needs_input", "needs": {"kind": "data", "need": "a planilha"}}
        return {"output": "ok"}

    async def go():
        from aiworkspace.chat import agent_mailbox

        agent_mailbox.current_ref.set("eq")
        membros = [{"name": n, "task": "t", "instructions": "i", "isolated": False} for n in ("A", "B")]
        return await team.run_team(run, membros, "g", lambda ev: None, None)

    res = asyncio.run(go())
    assert 'continue_agent=\\"eq#1\\"' in json.dumps(res["report"]) and "a planilha" in res["report"]
    visto = team.model_view(res)
    assert visto["members"][1] == {"agent": "B", "ok": True, "ref": "eq#1", "status": "needs_input",
                                   "needs": {"kind": "data", "need": "a planilha"}}


def test_pedido_encerra_o_loop_do_agente_com_relatorio_parcial():
    """request_from_lead devolve end_tool_loop: a volta seguinte é só-texto (o relatório)."""
    ofertas = []

    async def fake_stream(api_key, model, messages, *, tools=None, params=None,
                          modalities=None, base_url=None):
        ofertas.append(tools is not None)
        if tools is None:
            yield {"choices": [{"delta": {"content": "Parcial: falta a planilha."}, "finish_reason": "stop"}]}
        else:
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "r1", "function": {
                "name": "request_from_lead", "arguments": '{"kind":"data","need":"a planilha"}'}}]},
                "finish_reason": "tool_calls"}]}

    async def pedir(name, args):
        return {"ok": True, "end_tool_loop": True, "note": "write your partial report"}

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="faça",
                chat_system_prompt=None, params={}, session=TurnSession(user_id="u"),
                native_tools=NativeToolOpts(specs=[orch.request_from_lead_tool()], run=pedir))]
        eventos = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig
    assert ofertas == [True, False]  # pediu uma vez e redigiu, sem mais tools
    assert next(e for e in eventos if e["type"] == "done")["content"] == "Parcial: falta a planilha."


# ------------------------------ runner (turno aninhado) ------------------------------

def _runner(monkeypatch, capturas, eventos=None):
    async def provider(db, user, model):
        return "key", "url"

    async def sift(db, uid, mc, **kw):
        capturas.setdefault("sift", []).append(kw)
        g = kw.get("grant")
        meta = {"grant": {"granted": [p for p in g if p.startswith("web.")],
                          "not_granted": [p for p in g if not p.startswith("web.")]}} if g is not None else {}
        return NS(meta=meta)

    async def skills(db, user, mc):
        return []

    async def media(db, user, mc, atts):
        return orch.MediaOpts(attachments=atts, vision=True, genimage={"model": "x"}, image_output=True)

    async def fake_run_turn(**kw):
        capturas.setdefault("turns", []).append(kw)
        for ev in eventos or []:
            if ev == "ask":
                nt = kw["native_tools"]
                args = {"kind": "tool", "need": "code.exec.run para rodar os testes"}
                yield {"type": "tool_call", "name": "request_from_lead", "arguments": args}
                r = await nt.run("request_from_lead", args)
                yield {"type": "tool_result", "name": "request_from_lead", "result": r}
        yield {"type": "done", "content": "relatório parcial"}

    monkeypatch.setattr(ts, "_resolve_provider", provider)
    monkeypatch.setattr(ts, "get_sift_for_user", sift)
    monkeypatch.setattr(ts, "_load_skills", skills)
    monkeypatch.setattr(ts, "_media_opts", media)
    monkeypatch.setattr(ts, "run_turn", fake_run_turn)
    parent = NS(id="p1", base_model="m", params={}, tools_enabled=True, code_mode=False)
    # pool sem usuário: não consulta o orçamento no banco
    return ts._make_subagent_runner(None, NS(id="u1"), None, max_depth=1, parent=parent,
                                    pool=team.SubagentPool(8, 4, user_id=None))


async def test_runner_entrega_contexto_anexos_e_concessao_ao_turno_do_agente(monkeypatch):
    cap: dict = {}
    run = _runner(monkeypatch, cap)
    img = {"type": "image", "name": "a.png", "url": "data:image/png;base64,AAA"}
    out = await run("new", "revise", {"name": "Rev", "instructions": "revise",
                                      "tools": ["web.search.query", "code.exec.run"]},
                    handoff={"context": "PR #12", "attachments": [img]})
    turno = cap["turns"][0]
    assert turno["user_text"] == "revise\n\n## Context from the lead\nPR #12"
    assert turno["media"].attachments == [img] and turno["media"].vision is True
    assert turno["media"].genimage is None and turno["media"].image_output is False
    assert cap["sift"][0]["grant"] == ["web.search.query", "code.exec.run"]
    assert out["tools_granted"] == ["web.search.query"] and out["tools_not_granted"] == ["code.exec.run"]
    assert out["task"] == "revise" and out["attachments"] == ["a.png"]
    assert [s["function"]["name"] for s in turno["native_tools"].specs] == ["request_from_lead"]
    # o cache por turno é por escopo efetivo: sem concessão, outra montagem de tools
    await run("new", "outra", {"name": "X", "instructions": "i"})
    assert "grant" not in cap["sift"][1]


async def test_runner_marca_needs_input_quando_o_agente_pede(monkeypatch):
    cap: dict = {}
    run = _runner(monkeypatch, cap, eventos=["ask"])
    out = await run("new", "rode os testes", {"name": "QA", "instructions": "i"})
    assert out["status"] == "needs_input"
    assert out["needs"] == {"kind": "tool", "need": "code.exec.run para rodar os testes"}
    assert out["output"] == "relatório parcial"
    assert out["steps"][0] == {"tool": "request_from_lead", "detail": "code.exec.run para rodar os testes",
                               "ok": True}


async def test_conversa_pelo_painel_e_segundo_plano_nao_pedem_ao_orquestrador(monkeypatch):
    cap: dict = {}
    run = _runner(monkeypatch, cap)
    await run("new", "e agora?", {"name": "A", "instructions": "i"},
              prior=[{"role": "user", "content": "t"}, {"role": "assistant", "content": "r"}])
    assert cap["turns"][0]["native_tools"] is None
    await run("new", "t", {"name": "A", "instructions": "i"}, handoff={"ask_lead": False})
    assert cap["turns"][1]["native_tools"] is None
