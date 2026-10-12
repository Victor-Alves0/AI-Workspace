"""Saída do CLI `aiw` para os eventos SSE que o orquestrador emite (mesmas chaves)."""
from __future__ import annotations

import pytest

from aiworkspace.cli import render
from aiworkspace.cli.render import TurnView


@pytest.fixture(autouse=True)
def _sem_rich(monkeypatch):
    # saída determinística: texto cru (o rich só entra num terminal de verdade)
    monkeypatch.setattr(render, "Console", None)


def _run(events, pipe=False):
    v = TurnView(pipe=pipe)
    for ev in events:
        if not v.handle(ev):
            break
    v.finish()
    return v


def test_texto_ferramenta_subagente_e_uso(capsys):
    v = _run([
        {"type": "reasoning", "text": "hmm"},
        {"type": "reasoning", "text": "mais"},
        {"type": "token", "text": "Vou "},
        {"type": "token", "text": "buscar."},
        {"type": "tool_call", "name": "web.search", "arguments": '{"query": "clima hoje"}', "id": "c1"},
        {"type": "tool_result", "name": "web.search", "result": {"results": [1, 2]}},
        {"type": "tool_result", "name": "web.fetch", "result": {"error": "timeout"}},
        {"type": "subagent", "status": "start", "id": "s1", "agent": "pesquisador", "task": "achar fontes"},
        {"type": "subagent", "status": "done", "id": "s1", "agent": "pesquisador"},
        {"type": "token", "text": "Pronto."},
        {"type": "usage", "usage": {"total_tokens": 1500, "cost": 0.0021}},
        {"type": "title", "title": "Clima"},
        {"type": "done"},
        {"type": "token", "text": "depois do done não aparece"},
    ])
    out = capsys.readouterr().out
    assert out.count("pensando") == 1  # uma linha, não o raciocínio inteiro
    assert "Vou buscar.\n" in out
    assert "web.search" in out and "query=clima hoje" in out
    assert "timeout" in out
    assert "pesquisador" in out and "achar fontes" in out and "terminou" in out
    assert "Pronto." in out and "depois do done" not in out
    assert "1.5k tokens" in out and "$0.0021" in out
    assert v.title == "Clima" and v.text == "Vou buscar.Pronto."


def test_pipe_stdout_so_tem_a_resposta(capsys):
    _run([
        {"type": "tool_call", "name": "web.search", "arguments": {"q": "x"}},
        {"type": "token", "text": "resposta"},
        {"type": "usage", "usage": {"total_tokens": 10}},
        {"type": "done"},
    ], pipe=True)
    cap = capsys.readouterr()
    assert cap.out == "resposta\n"
    assert "web.search" in cap.err and "tokens" not in cap.err


def test_erro_e_interrupcao(capsys):
    v = _run([{"type": "token", "text": "meio"}, {"type": "error", "message": "provedor caiu"}])
    assert v.error == "provedor caiu"
    v2 = TurnView()
    v2.handle({"type": "stopped"})
    v2.finish()
    out = capsys.readouterr().out
    assert "erro: provedor caiu" in out and "(interrompido)" in out


def test_eventos_desconhecidos_sao_ignorados(capsys):
    v = _run([{"type": "evento_do_futuro", "x": 1}, {"type": "token", "text": "ok"}, {"type": "done"}])
    assert v.text == "ok"
