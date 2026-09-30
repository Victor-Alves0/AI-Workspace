"""Agentes/equipes em segundo plano: progresso ao vivo, placar p/ a IA, card gravado no
fim, delegação no turno acordado; + balão do "Enviar agora" na linha do tempo e títulos."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from aiworkspace.chat import activity, resume, subagent_jobs, titles


@pytest.fixture(autouse=True)
def _limpa():
    subagent_jobs._jobs.clear()
    subagent_jobs._pending.clear()
    subagent_jobs._patches.clear()
    yield
    subagent_jobs._jobs.clear()
    subagent_jobs._pending.clear()
    subagent_jobs._patches.clear()


def _job_de_equipe(n: int = 3) -> str:
    jid = "j1"
    subagent_jobs._jobs[jid] = {
        "chat_id": "c1", "name": "Wave 1", "task": "t", "status": "running", "started": 0.0,
        "live": {"members": [{"name": f"A{i}", "task": "x", "state": "queued", "timeline": []}
                             for i in range(n)], "synthesizing": False},
    }
    return jid


def test_progresso_da_equipe_vira_estado_por_membro():
    jid = _job_de_equipe()
    subagent_jobs.progress(jid, {"member": 0, "status": "running"})
    subagent_jobs.progress(jid, {"member": 0, "status": "progress", "tool": "web_search", "detail": "q"})
    subagent_jobs.progress(jid, {"member": 0, "status": "progress", "result": "web_search", "ok": True})
    subagent_jobs.progress(jid, {"member": 0, "status": "progress", "text": "achei "})
    subagent_jobs.progress(jid, {"member": 0, "status": "progress", "text": "isto"})
    subagent_jobs.progress(jid, {"member": 1, "status": "done", "ok": False})
    snap = subagent_jobs.snapshot("c1", jid)
    m0, m1, m2 = snap["members"]
    assert m0["state"] == "running"
    assert m0["timeline"][0] == {"kind": "tool", "tool": "web_search", "ok": True, "detail": "q"}
    assert m0["timeline"][1] == {"kind": "text", "text": "achei isto"}
    assert m1["state"] == "failed" and m2["state"] == "queued"
    # outro chat não enxerga o trabalho
    assert subagent_jobs.snapshot("outro", jid) is None


def test_placar_para_a_ia_so_com_o_que_roda():
    jid = _job_de_equipe(4)
    subagent_jobs.progress(jid, {"member": 0, "status": "done", "ok": True})
    subagent_jobs.progress(jid, {"member": 1, "status": "running"})
    bloco = subagent_jobs.status_block("c1")
    assert "Wave 1" in bloco and "1/4 agents finished" in bloco and "1 working" in bloco
    subagent_jobs._jobs[jid]["status"] = "done"
    assert subagent_jobs.status_block("c1") == ""


def test_equipe_terminada_deixa_o_card_para_gravar(monkeypatch):
    entregues = []
    monkeypatch.setattr(subagent_jobs, "_deliver", lambda chat, nota, jid=None: entregues.append(nota))

    async def run():
        return {"output": "relatório", "note": "2 of 2", "card": {"kind": "subagent_team", "members": []}}

    async def go():
        jid = subagent_jobs.start("c9", "Equipe", "objetivo", run, members=[{"name": "A", "task": "x"}])
        for _ in range(20):
            await asyncio.sleep(0)
        return jid

    jid = asyncio.run(go())
    assert subagent_jobs._jobs[jid]["status"] == "done"
    assert subagent_jobs._patches["c9"][0][0] == jid
    assert "relatório" in entregues[0]


def test_turno_acordado_recebe_a_delegacao():
    """Wave 2 dependia de delegate_team no turno do relatório da Wave 1 — o wake não
    passava `subagent=` e a IA 'procurava' a ferramenta sem achar."""
    src = inspect.getsource(resume.resume_chat_turn)
    assert "subagents_for_turn(" in src and "subagent=subagent_opts" in src


def test_enviar_agora_vira_passo_do_usuario_na_linha_do_tempo():
    tr = activity.ActivityTrace()
    tr.add({"type": "token", "text": "começando"})
    tr.add({"type": "steer", "text": "pesquise terror também"})
    assert tr.steps == [{"kind": "commentary", "text": "começando"},
                        {"kind": "user", "text": "pesquise terror também"}]


@pytest.mark.parametrize("raw, esperado", [
    ("<think>hmm</think>\nEquação dos Transformers", "Equação dos Transformers"),
    ("\n\n\"Title: Top jogos de 2026\".", "Top jogos de 2026"),
    ("I can't help with that", ""),
    ("x " * 80, None),
])
def test_limpeza_do_titulo(raw, esperado):
    t = titles._clean(raw)
    if esperado is None:
        assert 0 < len(t) <= titles.MAX_TITLE_CHARS
    else:
        assert t == esperado


def test_titulo_tenta_de_novo_quando_volta_vazio(monkeypatch):
    respostas = iter(["", "<think>só pensei</think>", "Receita de bolo"])
    chamadas = []

    async def fake_complete(api_key, model, messages, *, params=None, timeout=0, base_url=None):
        chamadas.append(messages[1]["content"])
        return next(respostas)

    monkeypatch.setattr(titles.openrouter, "complete", fake_complete)
    t = asyncio.run(titles.generate_title("k", "m", "me passa uma receita de bolo"))
    assert t == "Receita de bolo" and len(chamadas) == 3
    assert chamadas[0].startswith("Generate a title for this conversation:")
