"""Registro de gerações em andamento — desacopla a geração da IA do ciclo de
vida da request HTTP.

A geração roda numa ``asyncio.Task`` própria (o "driver"); a resposta SSE é
apenas um *assinante* do buffer de eventos. Se o cliente cai (F5, fechar a aba,
perda de rede), só o assinante morre — o driver continua até o fim e persiste a
resposta. Ao reabrir o chat, o front re-assina e vê a resposta continuar ao vivo
(ou já pronta). O buffer é replayável: um assinante atrasado recebe tudo desde o
início.

Como é tudo um único event loop (servidor de processo único, como o scheduler
das automações), um ``dict`` em memória basta. Se um dia houver múltiplos
workers, trocar o transporte por Redis pub/sub — a interface pública
(``start`` / ``get_active`` / ``Generation.subscribe``) não muda.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

# Quanto tempo manter a geração já concluída em memória após o "done", para um
# assinante atrasado (ex.: F5 logo depois de terminar) ainda conseguir drenar o
# buffer antes da limpeza.
_DONE_TTL = 45.0

# collected: estado terminal acumulado pelo driver, entregue ao on_finish.
Collected = dict
# on_finish(collected, emit): persiste o resultado e pode emitir eventos extras
# (ex.: "title") que também vão para os assinantes.
EmitFn = Callable[[dict], Awaitable[None]]
OnFinish = Callable[[Collected, EmitFn], Awaitable[None]]


class Generation:
    """Uma geração em andamento: buffer ordenado + notificação de novos eventos."""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.events: list[dict] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self.started_at = time.monotonic()
        self._cond = asyncio.Condition()

    async def _append(self, ev: dict) -> None:
        async with self._cond:
            self.events.append(ev)
            self._cond.notify_all()

    async def _finish(self) -> None:
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    def stop(self) -> bool:
        """Cancela o driver ("Parar" do usuário). O parcial já transmitido é
        persistido pelo caminho de CancelledError; retorna se havia o que parar."""
        if self.done or self.task is None or self.task.done():
            return False
        self.task.cancel()
        return True

    async def subscribe(self, start: int = 0) -> AsyncIterator[dict]:
        """Itera os eventos a partir do índice ``start`` até a geração terminar.

        Vários assinantes podem coexistir; cada um mantém seu próprio cursor. O
        cancelamento deste gerador (cliente desconectou) NÃO afeta o driver.
        """
        i = max(0, start)
        while True:
            async with self._cond:
                while i >= len(self.events) and not self.done:
                    await self._cond.wait()
                new = self.events[i:]
                i = len(self.events)
                finished = self.done
            for ev in new:
                yield ev
            if finished:
                return


_active: dict[str, Generation] = {}


def get_active(chat_id: str) -> Generation | None:
    """Geração em andamento (ou recém-concluída, dentro do TTL) para o chat."""
    return _active.get(chat_id)


def start(chat_id: str, source: AsyncIterator[dict], on_finish: OnFinish) -> Generation:
    """Inicia uma geração em background e devolve o ``Generation``.

    ``source`` é o gerador de eventos do turno (``run_turn``). O driver consome
    esse gerador numa task independente da request, acumula o estado terminal e
    chama ``on_finish`` para persistir — blindado contra cancelamento.
    """
    gen = Generation(chat_id)
    _active[chat_id] = gen

    async def _driver() -> None:
        collected: Collected = {
            "content": "",
            "usage": None,
            "reasoning": None,
            "tools": None,
            "memories": None,
            "streamed": "",
            # capturados incrementalmente para SOBREVIVER a um erro no meio do stream
            # (quando o `done` nunca chega): senão a resposta parcial/raciocínio somem.
            "reasoning_streamed": "",
            "error": None,
        }
        try:
            async for ev in source:
                t = ev.get("type")
                if t == "done":
                    collected["content"] = ev.get("content", "")
                    collected["usage"] = ev.get("usage")
                    collected["reasoning"] = ev.get("reasoning")
                    collected["tools"] = ev.get("tool_events")
                    collected["memories"] = ev.get("memories")
                elif t == "token":
                    collected["streamed"] += ev.get("text", "")
                elif t == "reasoning":
                    collected["reasoning_streamed"] += ev.get("text", "")
                elif t == "error":
                    collected["error"] = ev.get("message")
                await gen._append(ev)
        except asyncio.CancelledError:
            # "Parar" do usuário ou shutdown: salva o parcial e re-propaga.
            await gen._append({"type": "stopped"})
            await _finalize(gen, on_finish, collected)
            raise
        except Exception as exc:  # noqa: BLE001 - erro no turno vira evento visível
            logger.exception("Geração falhou (chat %s)", chat_id)
            await gen._append({"type": "error", "message": str(exc)})
        await _finalize(gen, on_finish, collected)

    gen.task = asyncio.create_task(_driver())
    return gen


async def _finalize(gen: Generation, on_finish: OnFinish, collected: Collected) -> None:
    """Persiste (blindado) e encerra a geração, agendando a expiração do buffer."""
    if gen.done:
        return  # já finalizada (ex.: caminho de CancelledError já rodou)
    try:
        # `shield`: mesmo se o driver estiver sendo cancelado (shutdown), o commit
        # da resposta completa em vez de se perder.
        await asyncio.shield(on_finish(collected, gen._append))
    except Exception:  # noqa: BLE001 - persistência best-effort; não derruba o loop
        logger.exception("Falha ao persistir geração (chat %s)", gen.chat_id)
    await gen._finish()
    asyncio.create_task(_expire(gen))


async def _expire(gen: Generation) -> None:
    await asyncio.sleep(_DONE_TTL)
    if _active.get(gen.chat_id) is gen:
        del _active[gen.chat_id]
