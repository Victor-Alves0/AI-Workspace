"""bg.spawn: mantém referência FORTE à task fire-and-forget até concluir.

O event loop só referencia uma `asyncio.Task` fracamente — se o chamador descarta o
retorno de `create_task`, o GC pode destruí-la no meio ("Task was destroyed but it is
pending!"), descartando o trabalho em silêncio. Regressão da varredura de concorrência:
continuação de fila (generation), wake de job (exec_jobs) e mensagem de canal de entrada
(whatsapp) eram disparadas com `create_task` nu e podiam sumir por coleta do GC."""
from __future__ import annotations

import asyncio
import gc

from aiworkspace import bg


def test_spawn_holds_ref_until_done_then_discards():
    async def go():
        ran = asyncio.Event()

        async def work():
            await asyncio.sleep(0.02)
            ran.set()

        t = bg.spawn(work())
        assert t in bg._TASKS            # referência forte enquanto pendente
        await t
        await asyncio.sleep(0)           # deixa o done_callback rodar
        assert ran.is_set()
        assert t not in bg._TASKS        # removida ao concluir (não vaza referência)

    asyncio.run(go())


def test_spawn_survive_retorno_descartado_e_gc():
    """Ignorar o retorno de bg.spawn é seguro: mesmo forçando um ciclo de GC na janela,
    a task conclui — é exatamente o caso fire-and-forget dos webhooks/wakes."""
    async def go():
        marker = {"done": False}

        async def work():
            await asyncio.sleep(0.03)
            marker["done"] = True

        bg.spawn(work())                 # retorno DESCARTADO de propósito
        gc.collect()                     # força o GC na janela pendente
        await asyncio.sleep(0.06)
        assert marker["done"] is True

    asyncio.run(go())
