"""App factory do FastAPI."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .admin_routes import router as admin_router
from .analytics_routes import router as analytics_router
from .artifacts_routes import router as artifacts_router
from .learning_routes import router as learning_router
from .memory_routes import router as memory_router
from .brain_routes import router as brain_router
from .knowledge_routes import router as knowledge_router
from .codespace_routes import router as codespace_router
from .share_routes import router as share_router
from .telegram_routes import router as telegram_router
from .discord_routes import router as discord_router
from .push_routes import router as push_router
from .security_routes import router as security_router
from .playground_routes import router as playground_router
from .auth.routes import router as auth_router
from .automation_routes import router as automation_router
from .automation import scheduler as automation_scheduler
from .chat.routes import router as chat_router
from . import network_config
from .config import get_settings
from .debug_routes import router as debug_router
from .folders_routes import router as folders_router
from .finance_routes import router as finance_router
from .integration_routes import router as integration_router
from .image_routes import router as image_router
from .models_routes import router as models_router
from .observability import install_logging, metrics
from .prompts_routes import router as prompts_router
from .settings_routes import router as settings_router
from .skills_routes import router as skills_router
from .tools_routes import router as tools_router
from .voice_routes import router as voice_router
from .whatsapp_routes import router as whatsapp_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aiworkspace")


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_logging()
    settings = get_settings()
    if settings.secret_is_insecure:
        msg = (
            "APP_SECRET inseguro/curto. Gere um valor forte: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
        if settings.is_production:
            raise RuntimeError(f"Recusando iniciar em produção: {msg}")
        logger.warning("⚠️  %s", msg)
    # carrega o allowlist de IP (aplicado pelo middleware) do banco
    try:
        await network_config.load_config()
    except Exception as exc:  # noqa: BLE001 - sem banco ainda? segue liberando geral
        logger.warning("Não foi possível carregar a config de rede (%s)", exc)
    # instâncias SIFT são construídas sob demanda, por usuário (tools.sift_service)
    # scheduler das automações: task asyncio única que dispara as vencidas
    try:
        automation_scheduler.start()
    except Exception as exc:  # noqa: BLE001 - o app sobe mesmo se o scheduler falhar
        logger.warning("Não foi possível iniciar o scheduler de automações (%s)", exc)
    # long-polling dos bots do Telegram conectados (uma task por conexão)
    try:
        from .integrations import telegram_poller
        await telegram_poller.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível iniciar os pollers do Telegram (%s)", exc)
    # gateways (WebSocket) dos bots do Discord conectados (uma task por conexão)
    try:
        from .integrations import discord_gateway
        await discord_gateway.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível iniciar os gateways do Discord (%s)", exc)
    # indexações da Base de Conhecimento interrompidas por restart (task em memória)
    try:
        from .knowledge import ingest as knowledge_ingest
        await knowledge_ingest.resume_pending()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível retomar indexações pendentes (%s)", exc)
    try:
        yield
    finally:
        await automation_scheduler.stop()
        try:
            from .integrations import telegram_poller
            await telegram_poller.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            from .integrations import discord_gateway
            await discord_gateway.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            from .tools.browser_driver import driver as browser_driver
            await run_in_threadpool(browser_driver.shutdown)
        except Exception:  # noqa: BLE001
            pass


def _client_ip(request: Request, trust_proxy: bool) -> str | None:
    """IP do cliente. Só confia no X-Forwarded-For quando TRUST_PROXY=1 (atrás de
    um proxy controlado) — senão o header é forjável."""
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Workspace API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # em dev, também reflete origens da LAN (localhost/IPs privados) p/ acesso
        # pelo IP da máquina (celular). Em produção é None (só as origens exatas).
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Timezone"],
    )

    @app.middleware("http")
    async def observe_and_harden(request: Request, call_next):
        start = time.perf_counter()
        # allowlist de IP (quando configurado pelo admin). /health fica isento p/
        # health checks locais/orquestradores não quebrarem.
        if request.url.path != "/health":
            ip = _client_ip(request, settings.trust_proxy)
            if not network_config.is_allowed(ip):
                metrics.record(request.method, request.url.path, 403, 0.0)
                return JSONResponse(status_code=403, content={"detail": "IP não autorizado"})
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            # garante que exceções não tratadas virem 500 limpo + métrica
            ms = (time.perf_counter() - start) * 1000
            metrics.record(request.method, request.url.path, 500, ms)
            logger.exception("Erro não tratado em %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": "Erro interno"})
        ms = (time.perf_counter() - start) * 1000
        metrics.record(request.method, request.url.path, response.status_code, ms)
        # cabeçalhos de segurança
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # HSTS só faz sentido sob HTTPS (origem https): força o navegador a só
        # falar TLS com a origem, mitigando downgrade/stripping.
        if settings.web_origin.startswith("https"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        response.headers["X-Server-Time-Ms"] = f"{ms:.1f}"
        return response

    @app.get("/health", tags=["meta"])
    async def health():
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(chat_router)
    app.include_router(folders_router)
    app.include_router(tools_router)
    app.include_router(models_router)
    app.include_router(prompts_router)
    app.include_router(skills_router)
    app.include_router(voice_router)
    app.include_router(finance_router)
    app.include_router(integration_router)
    app.include_router(whatsapp_router)
    app.include_router(image_router)
    app.include_router(automation_router)
    app.include_router(debug_router)
    app.include_router(admin_router)
    app.include_router(analytics_router)
    app.include_router(artifacts_router)
    app.include_router(memory_router)
    app.include_router(learning_router)
    app.include_router(knowledge_router)
    app.include_router(codespace_router)
    app.include_router(brain_router)
    app.include_router(share_router)
    app.include_router(telegram_router)
    app.include_router(discord_router)
    app.include_router(push_router)
    app.include_router(playground_router)
    app.include_router(security_router)
    return app


app = create_app()
