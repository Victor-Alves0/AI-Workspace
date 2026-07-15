"""Entrada dos canais: agregação (debounce) + serialização por conversa.

Comum a WhatsApp, Telegram e Discord. Resolve dois problemas distintos:

1. **Fragmentação.** Em mensageria as pessoas quebram um pensamento em várias
   mensagens ("oi", "tudo bem?", "queria saber uma coisa..."). Sem agregação cada
   uma vira um turno completo: N chamadas ao modelo, N respostas, caro e pouco
   natural. Com uma janela de silêncio elas viram UM turno com o texto inteiro.

2. **Corrida.** Duas mensagens rápidas na mesma conversa não podem rodar dois
   turnos concorrentes sobre o mesmo Chat: cada um montaria o histórico sem
   enxergar o outro e as respostas poderiam sair fora de ordem.

Por isso há DUAS chaves:
- `convo` (conexão + conversa) → o **lock**: um turno por vez naquela conversa.
- `sender` → o **debounce**: num grupo, as mensagens de duas pessoas diferentes
  não são coladas num mesmo turno (o modelo veria uma fala costurada de dois
  autores). Cada remetente tem sua própria janela.

Estado em memória (por processo). O canal já é best-effort: se o processo cair,
mensagens em janela se perdem — o mesmo que já acontecia com um turno em curso.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# teto de segurança: uma janela não pode virar um turno gigante (nem um vetor de
# flood). Ao atingir o teto, dispara na hora sem esperar o silêncio.
MAX_BATCH = 12
# teto da janela configurável (evita um debounce de horas por engano na UI)
MAX_WINDOW_SECONDS = 60

_locks: dict[str, asyncio.Lock] = {}
_pending: dict[str, list[dict[str, Any]]] = {}
_timers: dict[str, asyncio.Task] = {}

Runner = Callable[[list[dict[str, Any]]], Awaitable[None]]


def lock(convo: str) -> asyncio.Lock:
    """Lock da CONVERSA: garante um turno por vez (não por remetente)."""
    if convo not in _locks:
        _locks[convo] = asyncio.Lock()
    return _locks[convo]


def window(seconds: Any) -> float:
    """Normaliza a janela configurada (0/inválido = agregação desligada)."""
    try:
        s = float(seconds or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(s, MAX_WINDOW_SECONDS))


async def submit(
    *,
    convo: str,
    sender: str,
    msg: dict[str, Any],
    seconds: float,
    runner: Runner,
) -> None:
    """Entrega `msg` ao canal, respeitando a janela de agregação e o lock da conversa.

    `runner` recebe a LISTA de mensagens do lote (uma só, quando não há agregação).
    """
    if window(seconds) <= 0:
        await _run(convo, [msg], runner)
        return

    key = f"{convo}|{sender}"
    batch = _pending.setdefault(key, [])
    batch.append(msg)

    # a janela reinicia a cada mensagem nova: só dispara após o silêncio
    old = _timers.pop(key, None)
    if old is not None:
        old.cancel()

    if len(batch) >= MAX_BATCH:
        _pending.pop(key, None)
        await _run(convo, batch, runner)
        return

    _timers[key] = asyncio.create_task(_after_silence(key, convo, seconds, runner))


async def _after_silence(key: str, convo: str, seconds: float, runner: Runner) -> None:
    try:
        await asyncio.sleep(window(seconds))
    except asyncio.CancelledError:
        return  # chegou outra mensagem: a janela foi reiniciada por `submit`
    # a partir daqui NÃO há mais await até tirarmos o lote e o timer de cena — o
    # loop é single-thread, então `submit` não consegue cancelar um disparo em curso
    # (se cancelasse, o lote sumiria em silêncio).
    batch = _pending.pop(key, [])
    _timers.pop(key, None)
    if batch:
        await _run(convo, batch, runner)


async def _run(convo: str, batch: list[dict[str, Any]], runner: Runner) -> None:
    async with lock(convo):
        try:
            await runner(batch)
        except Exception:  # noqa: BLE001 - uma conversa nunca derruba o canal
            logger.exception("canal: falha ao processar lote (%s, %d msg)", convo, len(batch))
