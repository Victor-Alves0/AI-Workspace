"""Cotações para o card de finanças (StockCard).

A tool `finance.quote.get` (SIFT) já cobre o fluxo do modelo; esta rota existe p/
o FRONTEND trocar o período do card (abas 1D/5D/1M…) buscando de novo com as
mesmas preferências/segredos do usuário.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from . import finance
from .auth.deps import require_approved
from .db import get_db
from .models import User
from .search import web_search
from .secrets_service import (
    ALPHAVANTAGE_KEY,
    BRAVE_KEY,
    FINNHUB_KEY,
    TAVILY_KEY,
    get_secret,
)
from .tools import sift_service

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("/quote")
async def quote(
    symbol: str,
    range: str = "1d",
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    prefs = (user.profile or {}).get("finance")
    finnhub = await get_secret(db, user.id, FINNHUB_KEY)
    alpha = await get_secret(db, user.id, ALPHAVANTAGE_KEY)
    cfg = sift_service.finance_config_from_secrets(finnhub, alpha, prefs)

    # fallback web precisa de uma função de busca → injeta com os segredos do usuário
    if cfg.web_fallback or "web" in cfg.providers:
        tavily = await get_secret(db, user.id, TAVILY_KEY)
        brave = await get_secret(db, user.id, BRAVE_KEY)
        scfg = sift_service.search_config_from_secrets(
            tavily, brave, (user.profile or {}).get("web_search")
        )

        async def _ws(q: str) -> list[dict]:
            return await web_search(q, scfg)

        cfg.web_search = _ws

    q = await finance.fetch_quote(symbol, cfg, range)
    if q.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, q["error"])
    return q
