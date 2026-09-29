"""Caixa de texto no painel de um agente: a mensagem entra no trabalho do agente em
andamento (caixa de correio → steer); depois que ele termina, vira continuação com o
histórico dele; e a IA principal fica sabendo da conversa (nota no próximo turno)."""
from __future__ import annotations

import asyncio

from aiworkspace.chat import agent_followup, agent_mailbox


def test_caixa_so_entrega_para_agente_que_esta_trabalhando():
    k = agent_mailbox.key("chat1", "call_x#2")
    assert k == "chat1:call_x#2"
    assert agent_mailbox.send(k, "foca em PC") is False       # ninguém trabalhando
    agent_mailbox.open_box(k)
    try:
        assert agent_mailbox.send(k, "foca em PC") and agent_mailbox.send(k, "e resume")
        assert agent_mailbox.drain(k) == ["foca em PC", "e resume"]
        assert agent_mailbox.drain(k) == []                      # entregue uma vez só
    finally:
        agent_mailbox.close_box(k)
    assert agent_mailbox.send(k, "tarde demais") is False
    assert agent_mailbox.key(None, "call_x") is None and agent_mailbox.key("c", None) is None


def test_membro_da_equipe_tem_ref_propria():
    """run_team marca cada membro como `<id da chamada>#<n>` (o painel fala com ele)."""
    from aiworkspace.chat.subagent_team import run_team

    vistos: list[str | None] = []

    async def run(key, task, new, progress=None):
        vistos.append(agent_mailbox.current_ref.get())
        return {"output": "ok"}

    async def _go():
        agent_mailbox.current_ref.set("call_eq")
        membros = [{"name": f"A{i}", "task": "t", "instructions": "i", "isolated": False} for i in range(3)]
        return await run_team(run, membros, "objetivo", lambda ev: None, None)

    asyncio.run(_go())
    assert sorted(vistos) == ["call_eq#0", "call_eq#1", "call_eq#2"]


def test_continuacao_leva_tarefa_relatorio_e_conversa_anterior():
    view = {"task": "listar jogos", "output": "RELATÓRIO", "followups": [
        {"role": "user", "content": "e PC?"}, {"role": "assistant", "content": "no PC: X"}]}
    assert agent_followup.prior_history(view) == [
        {"role": "user", "content": "listar jogos"}, {"role": "assistant", "content": "RELATÓRIO"},
        {"role": "user", "content": "e PC?"}, {"role": "assistant", "content": "no PC: X"}]
    assert agent_followup.split_ref("call_1#3") == ("call_1", 3)
    assert agent_followup.split_ref("call_1") == ("call_1", None)


def test_nota_para_a_ia_principal_e_consumida_uma_vez():
    """take_notes_block devolve o bloco e apaga as notas (entram num turno só)."""
    class _Res:
        def __init__(self, v):
            self.v = v

        def scalar(self):
            return self.v

    class _Db:
        def __init__(self):
            self.valor = {"v": [{"agent": "Pesquisador", "user": "e no Switch?", "reply": "Switch em 2027"}]}

        async def execute(self, stmt, params=None):
            v, self.valor = self.valor, None
            return _Res(v)

    db = _Db()
    bloco = asyncio.run(agent_followup.take_notes_block(db, "c1"))
    assert "talked DIRECTLY" in bloco and "Pesquisador" in bloco and "Switch em 2027" in bloco
    assert asyncio.run(agent_followup.take_notes_block(db, "c1")) == ""
