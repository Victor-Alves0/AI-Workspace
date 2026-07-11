"""Debug de Tools (efêmero).

Dois modos:
  - **dispatch_tool** — chama UMA ferramenta direto (`sift.execute_tool(path, params)`)
    e devolve o resultado cru (já filtrado pelo SIFT, exatamente o que o modelo veria)
    + latência. Usa a instância SIFT COMPLETA do usuário (sem gating por-modelo).
  - **debug_turn** — roda um turno completo com um modelo (preset com ferramentas) num
    chat efêmero (não persistido) e repassa cada evento de ferramenta como SSE, para a
    UI montar a linha do tempo de `search_tools`/`execute_tool`/`run_code` + resposta.

Reusa os motores do chat (`_resolve_provider`, `_get_model_config`, `_load_skills`,
`get_sift_for_user`, `run_turn`) — sem persistência.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import User
from ..tools.loader import build_full_sift_for_user, get_sift_for_user


async def dispatch_tool(db: AsyncSession, user: User, path: str, params: dict) -> dict:
    """Executa uma ferramenta e devolve {result, latency_ms} ou {error}."""
    sift = await build_full_sift_for_user(db, user.id)
    if sift is None:
        return {"error": "Ferramentas indisponíveis (SIFT não inicializou)."}
    t0 = time.monotonic()
    try:
        result = await run_in_threadpool(sift.execute_tool, path, params or {})
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:400], "latency_ms": int((time.monotonic() - t0) * 1000)}
    return {"result": result, "latency_ms": int((time.monotonic() - t0) * 1000)}


async def debug_turn(user: User, model: str, model_config_id, prompt: str, user_tz: str = ""):
    """Roda um turno efêmero e gera os eventos do `run_turn` (tool_call/tool_result/
    token/done/error) para a UI. Não persiste nada.

    Abre a PRÓPRIA sessão para resolver provedor/SIFT/skills — a sessão da request
    fecha ao retornar o StreamingResponse, então não pode ser usada dentro do stream
    (mesmo cuidado da mesa-redonda)."""
    # import tardio evita ciclo com chat.routes em tempo de import
    from ..chat.routes import _code_mode, _get_model_config, _load_skills, _resolve_provider
    from ..chat.orchestrator import run_turn

    async with SessionLocal() as db:
        mc = None
        if model_config_id:
            try:
                mc = await _get_model_config(db, uuid.UUID(str(model_config_id)), user)
            except (ValueError, TypeError):
                mc = None
        # modelo de fato: base_model do preset, senão o id do modelo-base escolhido
        model_str = (mc.base_model if mc else model) or ""
        if not model_str:
            yield {"type": "error", "message": "Selecione um modelo."}
            return
        try:
            api_key, base_url = await _resolve_provider(db, user, model_str)
        except Exception as exc:  # noqa: BLE001 - HTTPException etc.
            yield {"type": "error", "message": getattr(exc, "detail", str(exc))}
            return
        sift = await get_sift_for_user(db, user.id, mc)
        skills = await _load_skills(db, user, mc)
        mc_system = mc.system_prompt if mc else None
        mc_params = (mc.params if mc else {}) or {}
        mc_code = _code_mode(mc)

    if sift is None:
        yield {"type": "notice", "message": "O modelo escolhido não tem ferramentas ativas — o trace não terá chamadas."}

    async for ev in run_turn(
        api_key=api_key,
        model=model_str,
        history=[],
        user_text=prompt,
        chat_system_prompt=mc_system,
        params=mc_params,
        user_id=str(user.id),
        user_tz=user_tz,
        base_url=base_url,
        sift=sift,
        skills=skills,
        use_tools=True,
        code_mode=mc_code,
        chat_id=None,
    ):
        yield ev
