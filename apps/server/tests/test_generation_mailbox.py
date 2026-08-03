"""Caixa de entrada da geração (steer/fila) + drenagem no fim do turno.

Cobre o mecanismo de enviar mensagem DURANTE uma geração ativa (docs/turn-pipeline.md):
enqueue particiona steer vs fila; a fila é drenada no FIM de um turno completo (dispara
`on_queue`), mas NÃO no cancelamento ("Parar" do usuário). E a regressão estrutural: o
send_message roteia o envio-durante-geração p/ enqueue (corrige a corrida de 2 gerações)."""
from __future__ import annotations

import asyncio
import inspect

from aiworkspace.chat import generation as gen_mod
from aiworkspace.chat.generation import Generation


def test_enqueue_partitions_steer_and_queue():
    g = Generation("cP")
    asyncio.run(g.enqueue("A", steer=True))
    asyncio.run(g.enqueue("B", steer=False))
    asyncio.run(g.enqueue("C", steer=True))
    assert g.drain_steer() == ["A", "C"]
    assert g.drain_steer() == []          # já drenado
    assert g.drain_queue() == ["B"]
    assert g.pending == []


def test_queue_drained_at_end_fires_on_queue():
    got: dict = {}

    async def on_finish(collected, emit):
        pass

    async def on_queue(texts):
        got["texts"] = texts

    release = asyncio.Event()

    async def src():
        yield {"type": "token", "text": "oi"}
        await release.wait()          # segura o turno até enfileirarmos
        yield {"type": "done", "content": "pronto", "usage": None, "tool_events": None}

    async def go():
        g = gen_mod.start("cQ", src(), on_finish, on_queue=on_queue)
        await asyncio.sleep(0)                     # deixa o driver consumir o 1º yield
        await g.enqueue("continue com isso", steer=False)
        release.set()
        await g.task
        await asyncio.sleep(0.02)                  # deixa o create_task(on_queue) rodar

    asyncio.run(go())
    assert got.get("texts") == ["continue com isso"]


def test_cancel_does_not_fire_on_queue():
    got = {"called": False}

    async def on_finish(collected, emit):
        pass

    async def on_queue(texts):
        got["called"] = True

    release = asyncio.Event()

    async def src():
        yield {"type": "token", "text": "oi"}
        await release.wait()          # nunca liberado — vamos cancelar
        yield {"type": "done", "content": "x", "usage": None, "tool_events": None}

    async def go():
        g = gen_mod.start("cC", src(), on_finish, on_queue=on_queue)
        await asyncio.sleep(0)
        await g.enqueue("nao deve disparar", steer=False)
        g.stop()                                   # "Parar" do usuário
        try:
            await g.task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.02)

    asyncio.run(go())
    assert got["called"] is False   # cancelamento não drena a fila


def test_send_message_routes_active_gen_to_enqueue():
    # regressão estrutural: o envio-durante-geração vai p/ enqueue, não abre 2ª geração
    from aiworkspace.chat import messages_routes as mr
    src = inspect.getsource(mr.send_message)
    assert "get_active(" in src, "send deve checar geração ativa"
    assert ".enqueue(" in src, "send deve enfileirar durante geração ativa"
    assert "queued" in src, "send deve retornar sinal de enfileirado"
