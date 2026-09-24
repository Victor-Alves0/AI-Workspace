"""Subagentes em SEGUNDO PLANO: a IA solta o agente e segue (ou encerra o turno); quando
ele termina, o chat é acordado num turno novo com o relatório (mesmo caminho do wake
dos comandos longos do Codespace, `resume.resume_chat_turn`).

Vários agentes que terminam juntos (ou enquanto o chat ainda gera) são entregues num
wake só: uma fila de notas por chat e um único "acordador" por chat, que espera o chat
ficar ocioso, junta tudo o que chegou e dispara o turno.

Registro em memória, por processo (como `generation._active`): um restart perde os
agentes em andamento — o turno deles também morreria.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .. import bg

logger = logging.getLogger(__name__)

NOTE_PREFIX = "[Agente em segundo plano concluído]"
_IDLE_TIMEOUT_S = 6 * 3600   # o turno principal pode seguir trabalhando por muito tempo
_REPORT_CHARS = 12000        # teto do relatório na nota (o resto fica no card do agente)

_jobs: dict[str, dict[str, Any]] = {}
_pending: dict[str, list[str]] = {}
_wakers: dict[str, asyncio.Task] = {}


def start(chat_id: str, name: str, task: str, run: Callable[[], Awaitable[dict]]) -> str:
    """Solta o agente; devolve o id do trabalho. `run()` executa o agente inteiro."""
    jid = uuid.uuid4().hex[:10]
    _jobs[jid] = {"chat_id": chat_id, "name": name, "task": task, "status": "running",
                  "started": time.time()}

    async def _go() -> None:
        try:
            result = await run()
        except Exception as exc:  # noqa: BLE001 - o erro vira o relatório
            logger.warning("subagente em segundo plano falhou (%s): %s", name, exc)
            result = {"error": f"o subagente falhou: {exc}"}
        _jobs.pop(jid, None)
        _deliver(chat_id, note_for(name, task, result))

    bg.spawn(_go(), name=f"subagent-bg-{jid}")
    return jid


def note_for(name: str, task: str, result: Any) -> str:
    """A nota (mensagem de usuário do wake) com o relatório do agente."""
    if isinstance(result, dict) and result.get("error"):
        corpo = f"Falhou: {result['error']}"
    else:
        saida = str((result or {}).get("output") if isinstance(result, dict) else result or "")
        corpo = saida[:_REPORT_CHARS] + ("\n[relatório cortado]" if len(saida) > _REPORT_CHARS else "")
        if isinstance(result, dict) and result.get("note"):
            corpo += f"\n\n{result['note']}"
    return (f"{NOTE_PREFIX} {name}\n\nTarefa: {task}\n\nRelatório:\n{corpo}\n\n"
            "Continue a partir deste resultado.")


def _deliver(chat_id: str, note: str) -> None:
    _pending.setdefault(chat_id, []).append(note)
    waker = _wakers.get(chat_id)
    if waker is None or waker.done():
        _wakers[chat_id] = bg.spawn(_waker(chat_id), name=f"subagent-wake-{chat_id}")


async def _waker(chat_id: str) -> None:
    from . import generation, resume  # lazy: resume importa meio chat

    try:
        while _pending.get(chat_id):
            fim = time.monotonic() + _IDLE_TIMEOUT_S
            while (g := generation.get_active(chat_id)) is not None and not g.done:
                if time.monotonic() > fim:
                    logger.warning("wake de subagente descartado: chat %s nunca ficou ocioso", chat_id)
                    _pending.pop(chat_id, None)
                    return
                await asyncio.sleep(1.0)
            notas = _pending.pop(chat_id, [])
            if not notas:
                break
            nomes = [n[len(NOTE_PREFIX):].split("\n", 1)[0].strip() for n in notas]
            await resume.resume_chat_turn(
                chat_id, "\n\n---\n\n".join(notas),
                notify_title="Agente em segundo plano concluído" if len(notas) == 1
                else f"{len(notas)} agentes em segundo plano concluídos",
                notify_body=", ".join(nomes)[:200],
            )
            await asyncio.sleep(1.0)  # a geração do wake se registra antes da próxima volta
    except Exception:  # noqa: BLE001 - wake é best-effort, nunca derruba o processo
        logger.exception("wake de subagentes falhou (chat %s)", chat_id)
    finally:
        _wakers.pop(chat_id, None)
