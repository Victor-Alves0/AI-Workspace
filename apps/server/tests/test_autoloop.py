"""Loop autônomo (self-continue): a lógica de decisão do after_turn.

Cobre as paradas calibradas: continua com Ledger ativo + próximo passo; para no done/
sem-passo (natural); para em card ask pendente; pausa+avisa no teto de iterações e na
convergência; halt e reset zeram/interrompem. Sem rodar turnos reais — monkeypatch de
ledger_service.load, _continue_when_idle e _notify_pause.
"""
from __future__ import annotations

import asyncio

from aiworkspace.chat import autoloop_service as al
from aiworkspace.chat import ledger_service


class FakeMC:
    def __init__(self, enabled=True, max_iter=6, cost_cap=0.0):
        self.capabilities = {"autonomous_loop": enabled}
        self.filter_config = {"autoloop": {"max_iterations": max_iter, "cost_cap_usd": cost_cap}}


def _install(monkey_led, sched, paused):
    async def fake_load(chat_id):
        return monkey_led["led"]
    async def fake_continue(chat_id, note):
        sched.append(note)
    async def fake_pause(chat_id, user_id, reason):
        paused.append(reason)
    ledger_service.load = fake_load
    al._continue_when_idle = fake_continue
    al._notify_pause = fake_pause


def _run(led, mc, collected=None, chat="c1"):
    sched, paused = [], []
    state = {"led": led}
    _install(state, sched, paused)
    # after_turn agenda via create_task(_continue_when_idle); rodamos e drenamos tasks
    async def go():
        await al.after_turn(chat_id=chat, user_id="u1", project_id="p1",
                            model_config=mc, collected=collected or {})
        await asyncio.sleep(0)  # deixa as tasks agendadas rodarem
    asyncio.run(go())
    return sched, paused


ACTIVE = {"status": "active", "next_step": "seguir o passo 2",
          "plan": [{"id": "s1", "status": "done"}, {"id": "s2", "status": "todo"}],
          "findings": []}


def test_disabled_model_never_continues():
    al.reset("c1")
    sched, paused = _run(ACTIVE, FakeMC(enabled=False))
    assert sched == [] and paused == []


def test_active_ledger_with_next_step_continues():
    al.reset("c1")
    sched, paused = _run(ACTIVE, FakeMC())
    assert len(sched) == 1 and paused == []


def test_done_ledger_stops_naturally():
    al.reset("c1")
    done = {**ACTIVE, "status": "done"}
    sched, paused = _run(done, FakeMC())
    assert sched == [] and paused == []


def test_no_pending_step_stops():
    al.reset("c1")
    nostep = {"status": "active", "next_step": "",
              "plan": [{"id": "s1", "status": "done"}], "findings": []}
    sched, paused = _run(nostep, FakeMC())
    assert sched == [] and paused == []


def test_pending_ask_card_stops():
    al.reset("c1")
    collected = {"tools": [{"kind": "result", "name": "x",
                            "data": {"kind": "ask", "question": "prosseguir?"}}]}
    sched, paused = _run(ACTIVE, FakeMC(), collected=collected)
    assert sched == [] and paused == []


def test_iteration_cap_pauses_and_notifies():
    al.reset("c1")
    mc = FakeMC(max_iter=2)
    # 1º e 2º continuam; o 3º bate o teto e pausa
    s1, p1 = _run(ACTIVE, mc); s2, p2 = _run(ACTIVE, mc); s3, p3 = _run(ACTIVE, mc)
    assert len(s1) == 1 and len(s2) == 1
    assert s3 == [] and len(p3) == 1 and "teto de 2" in p3[0]


def test_convergence_pauses():
    al.reset("c1")
    mc = FakeMC(max_iter=99)
    # mesmo Ledger repetido: muda no 1º (baseline), estagna nos seguintes até stall_limit
    outs = [_run(ACTIVE, mc) for _ in range(4)]
    paused_any = any(p for _, p in outs)
    assert paused_any, "convergência deveria pausar com Ledger imutável"


def test_halt_stops_and_reset_clears():
    al.reset("c1")
    al.halt("c1")
    sched, paused = _run(ACTIVE, FakeMC())
    assert sched == [] and paused == []
    al.reset("c1")  # nova mensagem do usuário limpa o halt
    sched2, _ = _run(ACTIVE, FakeMC())
    assert len(sched2) == 1


def test_cost_cap_pauses():
    al.reset("c1")
    mc = FakeMC(max_iter=99, cost_cap=1.0)
    sched, paused = _run(ACTIVE, mc, collected={"usage": {"cost": 2.5}})
    assert sched == [] and len(paused) == 1 and "custo" in paused[0]
