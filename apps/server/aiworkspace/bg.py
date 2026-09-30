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
import contextvars
from typing import Any, Coroutine

# referências FORTES às tasks em voo (o callback as remove ao concluir). Enquanto a task
# está aqui, o GC não a coleta — o loop sozinho só a referencia fracamente.
_TASKS: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None,
          trace: str | bool | None = None) -> asyncio.Task:
    """Agenda `coro` mantendo uma referência forte até concluir (evita coleta pelo GC).

    Devolve a `Task` (para quem quiser aguardá-la/cancelá-la); ignorar o retorno é seguro,
    diferente de `asyncio.create_task`.

    Observabilidade: a task herda o contexto de quem a disparou — inclusive o trace,
    que em geral já FECHOU quando ela roda (as etapas dela se perderiam). Então, se
    havia um trace ativo (ou `trace=` foi pedido), ela roda num trace PRÓPRIO ligado
    ao disparador (`parent_trace`): a cadeia fica navegável no painel.
    `trace=False`: timers que só esperam (expirar buffer) — sem trace, senão viram
    "operação em andamento" e suspeitos falsos das travadas.
    """
    from . import tracing

    parent = tracing.current_trace()
    if trace is not False and (trace is not None or parent is not None):
        nome = trace if isinstance(trace, str) else f"bg:{_sem_ids(name or getattr(coro, '__qualname__', 'task'))}"
        coro = _in_trace(coro, nome)
    # sem o livro de efeitos do turno que disparou: o trabalho em segundo plano vive
    # além dele e não é uma "nova tentativa" (ver tools/effects.py)
    from .tools import effects

    ctx = contextvars.copy_context()
    ctx.run(effects.current.set, None)
    task = asyncio.create_task(coro, name=name, context=ctx)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _in_trace(coro: Coroutine[Any, Any, Any], trace_name: str) -> Any:
    from . import tracing

    with tracing.linked_trace(trace_name, kind="worker", bg=True):
        return await coro


_ID_RE = None


def _sem_ids(nome: str) -> str:
    """"subagent-wake-<uuid do chat>" → "subagent-wake": ids no nome espalhariam a
    mesma operação em mil linhas na análise do painel."""
    global _ID_RE
    if _ID_RE is None:
        import re

        _ID_RE = re.compile(r"[-_:]?(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{10,}|\d{4,})", re.I)
    return _ID_RE.sub("", nome) or nome
