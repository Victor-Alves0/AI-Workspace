"""Comparação de modelos lado a lado (efêmera).

Roda o MESMO prompt em N modelos concorrentemente e emite os deltas de cada um
marcados com a coluna, para a UI streamar em paralelo. Usa `openrouter.stream_chat`
direto (resposta "crua" do modelo-base + params/system do preset, sem memória/tools)
— é uma comparação de modelos, não um turno de chat completo.

As colunas já vêm resolvidas (api_key/base_url/params) pela rota, pois a sessão da
request fecha ao retornar o StreamingResponse.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..providers import openrouter

logger = logging.getLogger(__name__)


def _extract_cost(usage: dict) -> float | None:
    cost = usage.get("cost")
    if cost is None and isinstance(usage.get("cost_details"), dict):
        cost = usage["cost_details"].get("upstream_inference_cost")
    return float(cost) if isinstance(cost, (int, float)) else None


async def _run_col(col_idx: int, col: dict, prompt: str, system: str, queue: asyncio.Queue) -> None:
    """Streama uma coluna, empurrando eventos {col, type, ...} na fila."""
    sys_parts = [p for p in [col.get("system_prompt") or "", system or ""] if p]
    messages: list[dict] = []
    if sys_parts:
        messages.append({"role": "system", "content": "\n\n".join(sys_parts)})
    messages.append({"role": "user", "content": prompt})
    usage_total: dict[str, Any] = {}
    t0 = time.monotonic()
    try:
        async for chunk in openrouter.stream_chat(
            col["api_key"], col["model"], messages,
            params=col.get("params") or {}, base_url=col.get("base_url"),
        ):
            choices = chunk.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    await queue.put({"col": col_idx, "type": "delta", "text": delta["content"]})
            if isinstance(chunk.get("usage"), dict):
                usage_total = chunk["usage"]
        latency = int((time.monotonic() - t0) * 1000)
        await queue.put({
            "col": col_idx, "type": "done",
            "prompt_tokens": usage_total.get("prompt_tokens"),
            "completion_tokens": usage_total.get("completion_tokens"),
            "cost": _extract_cost(usage_total) if usage_total else None,
            "latency_ms": latency,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("Coluna de comparação falhou: %s", exc)
        await queue.put({"col": col_idx, "type": "error", "message": str(exc)[:300]})


async def stream_compare(cols: list[dict], prompt: str, system: str = ""):
    """Gera eventos (dicts) de todas as colunas conforme chegam. Termina com all_done."""
    queue: asyncio.Queue = asyncio.Queue()
    tasks = [asyncio.create_task(_run_col(i, c, prompt, system, queue)) for i, c in enumerate(cols)]
    pending = len(tasks)
    # cada coluna emite exatamente um done|error; conta-os p/ saber quando encerrar
    finished = 0
    try:
        while finished < pending:
            ev = await queue.get()
            if ev.get("type") in ("done", "error"):
                finished += 1
            yield ev
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    yield {"type": "all_done"}
