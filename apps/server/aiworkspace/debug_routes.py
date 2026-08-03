"""Painel de debug — somente admin.

Expõe estado de runtime para diagnóstico: info do sistema, health profundo,
logs recentes, métricas de request, estado do cache SIFT e conectividade dos
provedores. Não devolve segredos (apenas se estão configurados ou não).
"""

from __future__ import annotations

import platform
import sys

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .auth.deps import require_admin
from .config import get_settings
from .db import get_db
from .models import User
from .observability import metrics, ring_handler
from .secrets_service import (
    BRAVE_KEY,
    OPENROUTER_KEY,
    TAVILY_KEY,
    VOICE_KEY,
    get_secret,
    has_secret,
)
from .tools import sift_service

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/info")
async def info(admin: User = Depends(require_admin)):
    s = get_settings()
    return {
        "version": __version__,
        "app_env": s.app_env,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "secret_insecure": s.secret_is_insecure,
        "signup_enabled": s.enable_signup,
        "web_search_provider": s.web_search_provider,
        "voice_base_url": s.voice_base_url,
        "openrouter_base_url": s.openrouter_base_url,
        "max_tool_iterations": s.max_tool_iterations,
        "tool_sandbox": {
            "timeout_s": s.tool_timeout_seconds,
            "cpu_s": s.tool_cpu_seconds,
            "mem_mb": s.tool_mem_mb,
        },
    }


@router.get("/health")
async def deep_health(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    components: dict[str, dict] = {}
    # banco
    try:
        await db.execute(text("SELECT 1"))
        components["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        components["database"] = {"ok": False, "error": str(exc)}
    # extensão pgvector
    try:
        row = await db.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        components["pgvector"] = {"ok": row.first() is not None}
    except Exception as exc:  # noqa: BLE001
        components["pgvector"] = {"ok": False, "error": str(exc)}

    # saúde das CAPACIDADES do harness (últimas 24h): mem0 no-op, síntese caindo p/
    # camada C, watchdog abortando tools, deadline do codegraph, etc. — a competência
    # do sistema, não só a latência das requisições.
    try:
        from . import health_service
        capabilities = await health_service.snapshot(db, hours=24)
    except Exception as exc:  # noqa: BLE001
        capabilities = {"ok": True, "error": str(exc), "capabilities": []}

    overall = all(c.get("ok") for c in components.values()) and capabilities.get("ok", True)
    return {"ok": overall, "components": components, "health": capabilities}


@router.get("/primitives")
async def primitives(
    days: int = 7,
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Medição dos PRIMITIVOS do harness: com que frequência cada um agiu (steering,
    anti-spin, síntese A/B/C, watchdog, output-guard, ledger, compactação) e com que
    desfecho, nos últimos `days` dias. Uso, não saúde — o outro eixo da observação."""
    from . import health_service
    return await health_service.primitive_metrics(db, days=days)


@router.get("/logs")
async def logs(limit: int = 200, level: str | None = None, admin: User = Depends(require_admin)):
    return {"logs": ring_handler.recent(limit=min(limit, 500), level=level)}


@router.get("/metrics")
async def request_metrics(admin: User = Depends(require_admin)):
    return metrics.snapshot()


@router.get("/sift")
async def sift_state(admin: User = Depends(require_admin)):
    return sift_service.cache_info()


@router.get("/providers")
async def providers(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    s = get_settings()

    async def reach(url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url)
            return {"reachable": True, "status": r.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"reachable": False, "error": str(exc)[:120]}

    result: dict[str, dict] = {
        "openrouter": {
            "key_configured": await has_secret(db, admin.id, OPENROUTER_KEY),
        },
        "voice": {
            "key_configured": await has_secret(db, admin.id, VOICE_KEY),
            **await reach(f"{s.voice_base_url}/models"),
        },
        "tavily": {"key_configured": await has_secret(db, admin.id, TAVILY_KEY)},
        "brave": {"key_configured": await has_secret(db, admin.id, BRAVE_KEY)},
    }
    # OpenRouter: testa a chave do admin de fato, se configurada
    key = await get_secret(db, admin.id, OPENROUTER_KEY)
    if key:
        result["openrouter"].update(
            await reach(f"{s.openrouter_base_url}/models")
        )
    if s.web_search_provider == "searxng":
        result["searxng"] = await reach(s.searxng_url)
    return result
