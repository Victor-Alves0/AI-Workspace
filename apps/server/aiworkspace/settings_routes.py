"""Rotas de configurações do usuário: segredos (chave OpenRouter) e listagem de modelos."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit_service, budget_service
from .app_config import ALLOW_SIGNUPS, get_setting
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
    request: Request,
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
    # auditoria: registra QUE segredo mudou (nunca o valor)
    await audit_service.record("secret_set", user_id=user.id, request=request, detail={"name": name})
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
    # a config de busca/finanças/deep search/segurança/navegador afeta a SIFT do usuário → invalida cache
    if any(k in changed for k in ("web_search", "finance", "deep_search", "security", "browser")):
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


@router.get("/usage/summary")
async def usage_summary(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Estado do orçamento pessoal do usuário (gasto do mês, teto, modo, bloqueio)."""
    return await budget_service.budget_state(db, user)


@router.get("/status")
async def system_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Checagens de prontidão POR-USUÁRIO (chave, modelo, integrações) + o
    orçamento. Itens de infra (banco/sidecar) só para o admin."""
    from sqlalchemy import func, select

    from .integrations import tuya_service, voice_service
    from .integrations import whatsapp_evolution as evolution
    from .models import GoogleAccount, WhatsAppConnection

    uid = user.id
    web = (user.profile or {}).get("web_search") or {}
    provider = (web.get("primary") or get_settings().web_search_provider or "duckduckgo")

    wa_rows = list(await db.scalars(
        select(WhatsAppConnection).where(WhatsAppConnection.user_id == uid)
    ))
    wa_connected = sum(1 for c in wa_rows if (c.state or {}).get("status") == "open")
    google_count = await db.scalar(
        select(func.count()).select_from(GoogleAccount).where(GoogleAccount.user_id == uid)
    )

    out: dict = {
        "openrouter_key": await has_secret(db, uid, OPENROUTER_KEY),
        "default_model": user.default_model,
        "web": {"provider": provider, "searxng_url": web.get("searxng_url") or get_settings().searxng_url},
        "voice": (await voice_service.get_provider(db, uid)) is not None,
        "whatsapp": {"count": len(wa_rows), "connected": wa_connected},
        "google": int(google_count or 0),
        "tuya": await tuya_service.is_configured(db, str(uid)),
        "budget": await budget_service.budget_state(db, user),
    }
    if user.role == "admin":
        out["admin"] = {
            "db": True,  # se chegou aqui, a sessão do banco respondeu
            "evolution_configured": evolution.configured(),
            "signup_open": bool(await get_setting(db, ALLOW_SIGNUPS, False)),
        }
    return out


class WebTestIn(BaseModel):
    provider: str = "duckduckgo"
    searxng_url: str = ""


@router.post("/test/web")
async def test_web_search(
    body: WebTestIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Testa um mecanismo de busca (SearXNG/DuckDuckGo/Tavily/Brave) com uma consulta
    de sonda. Devolve {ok, count, error} — sem tocar na config salva."""
    provider = (body.provider or "duckduckgo").strip().lower()
    tavily = await get_secret(db, user.id, TAVILY_KEY)
    brave = await get_secret(db, user.id, BRAVE_KEY)
    prefs = {"primary": provider, "providers": [provider], "multi": False,
             "searxng_url": body.searxng_url or ""}
    cfg = sift_service.search_config_from_secrets(tavily, brave, prefs)
    try:
        from .search import web_search
        results = await web_search("teste de conexão", cfg)
        return {"ok": len(results) > 0, "count": len(results),
                "error": None if results else "Nenhum resultado retornado"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "count": 0, "error": str(exc)[:200]}


class BrowserTestIn(BaseModel):
    ws_url: str = ""
    token: str = ""


@router.post("/test/browser")
async def test_browser(body: BrowserTestIn, user: User = Depends(require_approved)):
    """Testa a conexão com o Navegador headless (browserless via CDP). Devolve
    {ok, error} — conecta e fecha um contexto, sem abrir página."""
    from fastapi.concurrency import run_in_threadpool

    from .tools.browser_driver import driver
    endpoint = sift_service._browser_endpoint(
        {"ws_url": body.ws_url, "token": body.token, "enabled": True}
    )
    if not endpoint:
        return {"ok": False, "error": "Sem URL do navegador (configure ws_url ou BROWSER_WS_URL)"}
    try:
        await run_in_threadpool(driver.probe, endpoint)
        return {"ok": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


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
