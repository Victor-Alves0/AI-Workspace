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
from .api.deps import ApiError
from .api.keys_routes import router as api_keys_router
from .api.mgmt_routes import router as api_mgmt_router
from .api.v1_routes import router as api_v1_router
from .artifacts_routes import router as artifacts_router
from .learning_routes import router as learning_router
from .memory_routes import router as memory_router
from .brain_routes import router as brain_router
from .knowledge_routes import router as knowledge_router
from .codespace_routes import router as codespace_router
from .share_routes import router as share_router
from .telegram_routes import router as telegram_router
from .discord_routes import router as discord_router
from .slack_channel_routes import router as slack_channel_router
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
from .observability_routes import router as observability_router
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
    # observabilidade: instrumenta o engine (conta/cronometra cada query) e sobe o
    # escritor em lote dos traces. Envolto em try: nunca impede o app de subir.
    try:
        from . import tracing
        from .db import engine as _db_engine
        from .tracing import instrument as _obs_instrument
        _obs_instrument.install(_db_engine.sync_engine)
        await tracing.sink.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível iniciar a observabilidade (%s)", exc)
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
    # sockets (Socket Mode / WebSocket) dos apps do Slack conectados (canal)
    try:
        from .integrations import slack_socket
        await slack_socket.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível iniciar os sockets do Slack (%s)", exc)
    # indexações da Base de Conhecimento interrompidas por restart (task em memória)
    try:
        from .knowledge import ingest as knowledge_ingest
        await knowledge_ingest.resume_pending()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível retomar indexações pendentes (%s)", exc)
    # pré-aquece o mem0 dos usuários ativos em background: a construção do cliente
    # (embedder + pgvector) leva ~1-3s na 1ª vez e, sem isto, esse custo caía no
    # PRIMEIRO turno de chat de cada usuário após um restart. Não bloqueia o boot.
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_prewarm_memory())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível agendar o pré-aquecimento da memória (%s)", exc)
    # reaper dos worktrees isolados ociosos do Codespace (ciclo de vida das tarefas)
    _wt_reaper_task = None
    try:
        import asyncio as _asyncio
        _wt_reaper_task = _asyncio.create_task(_worktree_reaper())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível agendar o reaper de worktrees (%s)", exc)
    try:
        yield
    finally:
        if _wt_reaper_task is not None:
            _wt_reaper_task.cancel()
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
            from .integrations import slack_socket
            await slack_socket.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            from .tools.browser_driver import driver as browser_driver
            await run_in_threadpool(browser_driver.shutdown)
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import tracing
            await tracing.sink.stop()
        except Exception:  # noqa: BLE001
            pass


async def _worktree_reaper() -> None:
    """Descarta periodicamente os worktrees isolados ociosos além do TTL (limpeza do
    disco + ciclo de vida das tarefas do Codespace). Best-effort; nunca derruba o app."""
    import asyncio

    from .codespace import worktree_service

    while True:
        try:
            await asyncio.sleep(3600)
            n = await worktree_service.reap_stale()
            if n:
                logger.info("codespace: %d worktree(s) ociosos descartados pelo reaper", n)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("reaper de worktrees do Codespace falhou", exc_info=True)


async def _prewarm_memory() -> None:
    """Aquece o cliente mem0 de cada usuário ativo que tem chave do OpenRouter.
    Best-effort e em background: falhas viram log, nunca afetam o boot."""
    try:
        from sqlalchemy import select
        from fastapi.concurrency import run_in_threadpool

        from .db import SessionLocal
        from .memory import mem0_service
        from .models import User
        from .secrets_service import OPENROUTER_KEY, get_secret

        async with SessionLocal() as db:
            users = list(await db.scalars(
                select(User).where(User.is_active.is_(True), User.status == "active")
            ))
            keys: set[str] = set()
            for u in users:
                k = await get_secret(db, u.id, OPENROUTER_KEY)
                if k:
                    keys.add(k)
        warmed = 0
        for k in keys:
            if await run_in_threadpool(mem0_service.warm, k):
                warmed += 1
        if warmed:
            logger.info("mem0 pré-aquecido para %d usuário(s)", warmed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pré-aquecimento da memória falhou (%s)", exc)


def _client_ip(request: Request, trust_proxy: bool) -> str | None:
    """IP do cliente. Só confia no X-Forwarded-For quando TRUST_PROXY=1 (atrás de
    um proxy controlado) — senão o header é forjável."""
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else None


# rotas que NÃO viram trace: health (ruído dos health checks) e a leitura da
# própria observabilidade (evita rastrear quem lê o rastro).
_NO_TRACE_PREFIXES = ("/health", "/observability", "/debug")


def _should_trace(path: str) -> bool:
    return not path.startswith(_NO_TRACE_PREFIXES)


def _route_template(request: Request, fallback: str) -> str:
    """Padrão da rota casada (ex.: /chats/{id}) em vez do path com o UUID — mantém a
    cardinalidade baixa para agregar por rota. Só existe DEPOIS do roteamento."""
    route = request.scope.get("route")
    tmpl = getattr(route, "path", None)
    return tmpl or fallback


def _trace_kind(path: str) -> str:
    if path.startswith("/v1"):
        return "api"
    if path.startswith(("/chats", "/roundtable")):
        return "chat"
    return "http"


async def _run_traced(request: Request, call_next, start: float, traced: bool):
    """Roda o call_next dentro (ou fora) de um trace. Devolve (response, ms,
    trace_id). response=None sinaliza exceção não tratada (o middleware responde
    500). O trace nunca altera o resultado — só observa."""
    from . import tracing

    path = request.url.path
    if not traced:
        try:
            resp = await call_next(request)
        except Exception:  # noqa: BLE001
            ms = (time.perf_counter() - start) * 1000
            metrics.record(request.method, path, 500, ms)
            logger.exception("Erro não tratado em %s %s", request.method, path)
            return None, ms, None
        return resp, (time.perf_counter() - start) * 1000, None

    try:
        with tracing.start_trace(
            f"{request.method} {path}", kind=_trace_kind(path),
            method=request.method, path=path,
        ) as tr:
            resp = await call_next(request)
            tr.status_code = resp.status_code
            # reescreve para o TEMPLATE agora que o roteamento aconteceu
            tmpl = _route_template(request, path)
            tr.path = tmpl
            tr.name = f"{request.method} {tmpl}"
            return resp, (time.perf_counter() - start) * 1000, tr.id
    except Exception:  # noqa: BLE001 - start_trace já marcou o erro e fez flush
        ms = (time.perf_counter() - start) * 1000
        metrics.record(request.method, path, 500, ms)
        logger.exception("Erro não tratado em %s %s", request.method, path)
        return None, ms, None


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

    # CORS da API pública: ela autentica por Bearer, nunca por cookie, então liberar
    # qualquer origem NÃO expõe a sessão do navegador — e com "*" o próprio browser
    # proíbe credenciais na resposta. Sem isto, uma aplicação web que chamasse /v1
    # direto do cliente esbarraria no CORS.
    _API_CORS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, X-API-Key",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Max-Age": "600",
    }

    @app.middleware("http")
    async def observe_and_harden(request: Request, call_next):
        start = time.perf_counter()
        # allowlist de IP (quando configurado pelo admin). /health fica isento p/
        # health checks locais/orquestradores não quebrarem. Vem ANTES do preflight
        # de CORS: com o atalho do OPTIONS na frente, um IP bloqueado ainda recebia
        # 204 e confirmava que o servidor existe.
        if request.url.path != "/health":
            ip = _client_ip(request, settings.trust_proxy)
            if not network_config.is_allowed(ip):
                metrics.record(request.method, request.url.path, 403, 0.0)
                return JSONResponse(status_code=403, content={"detail": "IP não autorizado"})
        if request.method == "OPTIONS" and request.url.path.startswith("/v1"):
            return JSONResponse(status_code=204, content=None, headers=_API_CORS)

        # cada requisição vira um TRACE raiz. /health e a própria API de leitura de
        # traces ficam de fora (ruído / recursão). O trace envolve o call_next
        # inteiro, então todo span aberto nas rotas/serviços aninha por baixo dele.
        traced = _should_trace(request.url.path)
        response, ms, trace_id = await _run_traced(
            request, call_next, start, traced
        )
        if response is None:  # exceção não tratada
            return JSONResponse(status_code=500, content={"detail": "Erro interno"})

        metrics.record(request.method, request.url.path, response.status_code, ms)
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id
        if request.url.path.startswith("/v1"):
            response.headers.update(_API_CORS)
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

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        """Erros da API pública saem no formato da OpenAI — é o que os SDKs
        existentes sabem interpretar (`error.message` / `error.code`)."""
        return JSONResponse(status_code=exc.status, content=exc.payload(),
                            headers=exc.headers or None)

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
    app.include_router(slack_channel_router)
    app.include_router(push_router)
    app.include_router(playground_router)
    app.include_router(security_router)
    app.include_router(observability_router)
    # API pública: /v1/* (Bearer token) + /api-keys (painel, cookie)
    app.include_router(api_v1_router)
    app.include_router(api_mgmt_router)
    app.include_router(api_keys_router)
    return app


app = create_app()
