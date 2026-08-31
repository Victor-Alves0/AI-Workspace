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


def test_source_exception_is_preserved_for_persistence():
    """Uma exceção levantada pelo source deve chegar ao ``on_finish``.

    O SSE de erro pode não ter assinante (aba fechada); a persistência é o único
    caminho que garante que o usuário veja por que a resposta parcial parou.
    """
    saved: dict = {}

    async def on_finish(collected, emit):
        saved.update(collected)

    async def src():
        yield {"type": "token", "text": "parcial"}
        raise RuntimeError("provider caiu")

    async def go():
        cid = "chat-source-error"
        gen_mod._active.pop(cid, None)
        try:
            g = gen_mod.start(cid, src(), on_finish)
            await g.task
            assert g.done is True
            assert any(
                e.get("type") == "error" and "provider caiu" in e.get("message", "")
                for e in g.events
            )
        finally:
            gen_mod._active.pop(cid, None)

    asyncio.run(go())
    assert saved["streamed"] == "parcial"
    assert saved["error"] == "provider caiu"


def test_usage_before_source_error_is_preserved():
    saved: dict = {}

    async def on_finish(collected, emit):
        saved.update(collected)

    async def src():
        yield {"type": "usage", "usage": {
            "prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14,
        }}
        raise RuntimeError("terminal incompleto")

    async def go():
        cid = "chat-usage-error"
        gen_mod._active.pop(cid, None)
        try:
            await gen_mod.start(cid, src(), on_finish).task
        finally:
            gen_mod._active.pop(cid, None)

    asyncio.run(go())
    assert saved["usage"]["total_tokens"] == 14
    assert saved["error"] == "terminal incompleto"


def test_start_single_flight_nunca_abre_2a_geracao():
    """REGRESSÃO (dinheiro): dois caminhos que checaram 'sem geração ativa' e então
    esperaram (awaits de setup) podiam ambos chamar `start` → 2 drivers no MESMO chat =
    chamada de modelo dobrada + respostas intercaladas. Gatilho sem usuário: o reaper do
    exec_jobs e o _ready_poller do preview disparam wakes independentes no mesmo chat.
    `start` é single-flight: enquanto a 1ª está ativa, a 2ª devolve a MESMA geração e NÃO
    consome o 2º source (nenhum 2º turno roda)."""
    consumed = {"a": 0, "b": 0}
    release = asyncio.Event()

    async def on_finish(collected, emit):
        pass

    async def src_a():
        consumed["a"] += 1
        yield {"type": "token", "text": "A"}
        await release.wait()                       # mantém a geração ATIVA
        yield {"type": "done", "content": "A", "usage": None, "tool_events": None}

    async def src_b():                             # não deve ser consumido enquanto A vive
        consumed["b"] += 1
        yield {"type": "done", "content": "B", "usage": None, "tool_events": None}

    async def go():
        cid = "chat-single-flight"
        gen_mod._active.pop(cid, None)
        try:
            g1 = gen_mod.start(cid, src_a(), on_finish)
            await asyncio.sleep(0)                  # deixa o driver de A consumir o 1º yield
            g2 = gen_mod.start(cid, src_b(), on_finish)   # COLISÃO: A ainda ativa
            assert g1 is g2, "start deve devolver a geração ativa, não abrir uma 2ª"
            assert gen_mod._active.get(cid) is g1
            assert consumed == {"a": 1, "b": 0}, f"B não podia rodar: {consumed}"
            # depois que A termina, start abre normalmente (não fica travado p/ sempre)
            release.set()
            await g1.task
            await asyncio.sleep(0.02)
            g1.done = True                          # garante 'não-ativa' (TTL ainda no _active)
            g3 = gen_mod.start(cid, src_b(), on_finish)
            assert g3 is not g1, "com a anterior concluída, start deve abrir uma nova"
            await asyncio.sleep(0.02)
            assert consumed["b"] == 1
        finally:
            gen_mod._active.pop(cid, None)

    asyncio.run(go())


def test_send_message_routes_active_gen_to_enqueue():
    # regressão estrutural: o envio-durante-geração vai p/ enqueue, não abre 2ª geração
    from aiworkspace.chat import messages_routes as mr
    src = inspect.getsource(mr.send_message)
    assert "get_active(" in src, "send deve checar geração ativa"
    assert ".enqueue(" in src, "send deve enfileirar durante geração ativa"
    assert "queued" in src, "send deve retornar sinal de enfileirado"
