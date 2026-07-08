"""Rotas de configurações do usuário: segredos (chave OpenRouter) e listagem de modelos."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .config import get_settings
from .db import get_db
from .models import User
from .schemas.auth import ProfileIn
from .providers import openrouter
from .secrets_service import (
    ALPHAVANTAGE_KEY,
    BRAVE_KEY,
    FINNHUB_KEY,
    IMAGEGEN_KEY,
    OPENROUTER_KEY,
    TAVILY_KEY,
    VOICE_KEY,
    get_secret,
    has_secret,
    set_secret,
)
from .tools import sift_service

router = APIRouter(prefix="/settings", tags=["settings"])

# nome amigável -> nome interno do segredo
_SECRET_NAMES = {
    "openrouter": OPENROUTER_KEY,
    "tavily": TAVILY_KEY,
    "brave": BRAVE_KEY,
    "voice": VOICE_KEY,
    "finnhub": FINNHUB_KEY,
    "alphavantage": ALPHAVANTAGE_KEY,
    "imagegen": IMAGEGEN_KEY,
}

# segredos que afetam a instância SIFT do usuário (busca/finanças/deep search) → invalidar cache
_SIFT_AFFECTING = {"tavily", "brave", "finnhub", "alphavantage", "openrouter"}


class SecretIn(BaseModel):
    api_key: str


@router.get("/secrets")
async def list_secret_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Informa apenas SE cada segredo existe — nunca devolve o valor em claro."""
    return {
        friendly: await has_secret(db, user.id, internal)
        for friendly, internal in _SECRET_NAMES.items()
    }


@router.put("/secrets/{name}")
async def set_secret_route(
    name: str,
    body: SecretIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    internal = _SECRET_NAMES.get(name)
    if internal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Segredo desconhecido")
    if not body.api_key.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chave vazia")
    await set_secret(db, user.id, internal, body.api_key.strip())
    # chaves de busca/finanças afetam a instância SIFT do usuário
    if name in _SIFT_AFFECTING:
        sift_service.invalidate(str(user.id))
    return {"ok": True}


class DefaultModelIn(BaseModel):
    model: str


@router.put("/default-model")
async def set_default_model(
    body: DefaultModelIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    user.default_model = body.model or None
    await db.commit()
    return {"ok": True, "default_model": user.default_model}


@router.put("/profile")
async def update_profile(
    body: ProfileIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Atualiza o perfil/preferências do usuário (merge parcial)."""
    changed = body.model_dump(exclude_unset=True)
    prof = dict(user.profile or {})
    prof.update(changed)
    user.profile = prof  # reatribui p/ o ORM detectar a mudança do JSONB
    await db.commit()
    # a config de busca/finanças/deep search afeta a SIFT do usuário → invalida cache
    if any(k in changed for k in ("web_search", "finance", "deep_search")):
        sift_service.invalidate(str(user.id))
    return {"ok": True, "profile": prof}


@router.get("/search")
async def search_config(user: User = Depends(require_approved)):
    """Config de busca em vigor (provider/url vêm do ambiente; chaves são por usuário)."""
    s = get_settings()
    return {
        "provider": s.web_search_provider,
        "searxng_url": s.searxng_url,
        "max_results": s.web_search_max_results,
        "available_providers": ["duckduckgo", "searxng", "tavily", "brave"],
    }


@router.get("/models")
async def list_models(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    if not api_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Configure sua chave do OpenRouter primeiro"
        )
    models = await openrouter.list_models(api_key)
    # devolve um subconjunto enxuto p/ o seletor de modelo
    return [
        {
            "id": m.get("id"),
            "name": m.get("name", m.get("id")),
            "context_length": m.get("context_length"),
            "pricing": m.get("pricing"),
        }
        for m in models
    ]


@router.get("/about")
async def about(user: User = Depends(require_approved)):
    """Versão do app + checagem best-effort de nova versão (release do GitHub).

    Disponível a qualquer usuário aprovado (a checagem de update do admin exige
    admin). Se o repositório não estiver configurado, só devolve a versão local."""
    import httpx

    from . import __version__, network_config

    out: dict = {
        "version": __version__,
        "latest_version": None,
        "update_available": False,
        "repo_url": None,
    }
    try:
        cfg = await network_config.load_config()
        repo = (cfg.get("repo") or "").strip()
    except Exception:  # noqa: BLE001
        repo = ""
    if not repo:
        return out
    out["repo_url"] = f"https://github.com/{repo}"
    try:
        async with httpx.AsyncClient(timeout=6, headers={"Accept": "application/vnd.github+json"}) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code == 200:
                tag = (r.json() or {}).get("tag_name")
                if tag:
                    out["latest_version"] = tag
                    out["update_available"] = tag.lstrip("v") != __version__.lstrip("v")
    except Exception:  # noqa: BLE001
        pass  # offline / sem release: mantém só a versão local
    return out
