"""Disparo de tarefas em segundo plano que NÃO podem ser coletadas pelo GC.

`asyncio.create_task(coro)` só é referenciada FRACAMENTE pelo event loop: se o chamador
não guardar o retorno numa referência forte, o coletor de lixo pode destruir a task no
meio da execução ("Task was destroyed but it is pending!"), descartando o trabalho em
silêncio. Em caminhos fire-and-forget consequentes — continuação de fila, wake de job,
processamento de mensagem de canal, broadcast, push — isso vira PERDA de trabalho do
usuário sem erro visível.

Vários módulos já resolvem isso com um `set` de módulo + `add_done_callback(discard)`;
este helper centraliza esse padrão numa chamada. Use `bg.spawn(coro())` no lugar de
`asyncio.create_task(coro())` para trabalho fire-and-forget que precisa concluir.
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine

# referências FORTES às tasks em voo (o callback as remove ao concluir). Enquanto a task
# está aqui, o GC não a coleta — o loop sozinho só a referencia fracamente.
_TASKS: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Agenda `coro` mantendo uma referência forte até concluir (evita coleta pelo GC).

    Devolve a `Task` (para quem quiser aguardá-la/cancelá-la); ignorar o retorno é seguro,
    diferente de `asyncio.create_task`.
    """
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
