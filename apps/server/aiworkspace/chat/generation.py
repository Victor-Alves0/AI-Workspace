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
        # preenchido quando a geração é cancelada por SHUTDOWN do servidor (≠ do
        # "Parar" do usuário): vira uma nota no parcial salvo ("interrompida porque…").
        self.interrupted_reason: str | None = None
        self._cond = asyncio.Condition()
        # Caixa de entrada: mensagens que o usuário enviou DURANTE esta geração. Cada
        # item {"text", "steer"}. steer=True é drenado pelo loop do turno em curso
        # (injeção em tempo real); steer=False é drenado no FIM do turno (fila →
        # turno de continuação). Preenchido pela rota, drenado pelo loop/driver.
        self.pending: list[dict] = []

    async def enqueue(self, text: str, *, steer: bool) -> None:
        """Adiciona uma mensagem enviada durante a geração (steer ou fila)."""
        async with self._cond:
            self.pending.append({"text": text, "steer": bool(steer)})
            self._cond.notify_all()
        await self._append({"type": "queued", "text": text[:200], "steer": bool(steer)})

    def drain_steer(self) -> list[str]:
        """Remove e devolve as mensagens de STEER (injeção no turno em curso). Sync:
        roda no mesmo event loop do loop do turno, entre awaits — sem corrida."""
        texts = [p["text"] for p in self.pending if p.get("steer")]
        if texts:
            self.pending = [p for p in self.pending if not p.get("steer")]
        return texts

    def drain_queue(self) -> list[str]:
        """Remove e devolve as mensagens de FILA (turno de continuação no fim)."""
        texts = [p["text"] for p in self.pending if not p.get("steer")]
        if texts:
            self.pending = [p for p in self.pending if p.get("steer")]
        return texts

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


def start(chat_id: str, source: AsyncIterator[dict], on_finish: OnFinish,
          on_queue: Callable[[list[str]], Awaitable[None]] | None = None) -> Generation:
    """Inicia uma geração em background e devolve o ``Generation``.

    ``source`` é o gerador de eventos do turno (``run_turn``). O driver consome
    esse gerador numa task independente da request, acumula o estado terminal e
    chama ``on_finish`` para persistir — blindado contra cancelamento.

    ``on_queue(texts)`` (opcional): chamado ao FIM de um turno COMPLETO se o usuário
    enfileirou mensagens durante ele (dispara um turno de continuação). Não roda no
    "Parar" (cancelamento) — o usuário parou de propósito.
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
            # logs das ferramentas acumulados AO VIVO — para um parcial (erro/parar/
            # shutdown) preservar o que a IA já executou, não só o texto.
            "tools_streamed": [],
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
                elif t == "tool_call":
                    collected["tools_streamed"].append(
                        {"kind": "call", "name": ev.get("name"), "data": ev.get("arguments")}
                    )
                elif t == "tool_result":
                    collected["tools_streamed"].append(
                        {"kind": "result", "name": ev.get("name"), "data": ev.get("result")}
                    )
                elif t == "error":
                    collected["error"] = ev.get("message")
                await gen._append(ev)
        except asyncio.CancelledError:
            # "Parar" do usuário ou shutdown: salva o parcial e re-propaga. Se foi
            # shutdown, deixa uma nota explicando (o "Parar" é intencional → sem nota).
            if gen.interrupted_reason and not collected["error"]:
                collected["error"] = gen.interrupted_reason
            await gen._append({"type": "stopped"})
            await _finalize(gen, on_finish, collected)
            raise
        except Exception as exc:  # noqa: BLE001 - erro no turno vira evento visível
            logger.exception("Geração falhou (chat %s)", chat_id)
            await gen._append({"type": "error", "message": str(exc)})
        await _finalize(gen, on_finish, collected)
        # fim de turno COMPLETO: se o usuário enfileirou (ou deixou steer não-consumido),
        # dispara a continuação. Não roda no cancelamento (CancelledError re-propaga antes).
        leftover = gen.drain_queue() + gen.drain_steer()
        if leftover and on_queue is not None:
            asyncio.create_task(on_queue(leftover))

    gen.task = asyncio.create_task(_driver())
    return gen


async def _finalize(gen: Generation, on_finish: OnFinish, collected: Collected) -> None:
    """Persiste (blindado) e encerra a geração, agendando a expiração do buffer."""
    if gen.done:
        return  # já finalizada (ex.: caminho de CancelledError já rodou)
    # parcial (o `done` não chegou): usa os logs de tools acumulados ao vivo, senão
    # um turno interrompido salvaria o texto mas PERDERIA o que a IA já executou.
    if not collected.get("tools") and collected.get("tools_streamed"):
        collected["tools"] = collected["tools_streamed"]
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


async def shutdown(timeout: float = 8.0) -> None:
    """No encerramento do servidor (SIGTERM/deploy/restart), cancela os drivers em
    andamento e AGUARDA a persistência do parcial de cada um. Sem isto, um restart
    no meio de um turno longo perdia o turno inteiro (texto + logs de tools).

    Cada driver cancelado salva o parcial pelo caminho de ``CancelledError`` (blindado
    por ``shield``); aqui só disparamos o cancelamento e esperamos, dentro do
    período de graça do processo (Docker manda SIGTERM e espera antes do SIGKILL)."""
    gens = [g for g in list(_active.values()) if not g.done and g.task and not g.task.done()]
    if not gens:
        return
    logger.info("Encerrando: salvando o parcial de %d geração(ões) em andamento", len(gens))
    for g in gens:
        g.interrupted_reason = "o servidor reiniciou; salvei o que já havia sido gerado até aqui"
        g.task.cancel()
    try:
        await asyncio.wait([g.task for g in gens], timeout=timeout)
    except Exception:  # noqa: BLE001 — best-effort; não trava o shutdown
        logger.exception("Falha ao aguardar o flush das gerações no encerramento")
