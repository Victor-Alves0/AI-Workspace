"""`@traced("setup:x")`: mede uma função async inteira como um span.

Para as etapas de preparação do turno (carregar ferramentas, skills, agentes,
anexos…): uma linha acima do `def` e a etapa aparece no waterfall com duração e o
banco que ela usou. Sem trace ativo, custa um contextvar lido.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .context import span

T = TypeVar("T")


def traced(name: str, *, kind: str = "internal") -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def deco(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            with span(name, kind=kind):
                return await fn(*args, **kwargs)
        return wrapper
    return deco
