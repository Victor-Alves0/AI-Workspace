"""Autenticação e guardas da API pública (Bearer token).

`require_api_key` é a porta única: valida a credencial, o estado da chave, o IP de
origem e o escopo, e devolve um `ApiContext` com tudo que a rota precisa. Nenhuma
rota da API deve ler o header Authorization por conta própria.

Erros seguem o formato da OpenAI (``{"error": {...}}``) — é o que os SDKs existentes
sabem ler, e o objetivo declarado é que um cliente OpenAI funcione sem adaptador.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ApiKey, User
from . import keys_service, limits


class ApiError(Exception):
    """Erro de API no formato OpenAI. Convertido em resposta pelo handler."""

    def __init__(self, message: str, *, status: int = 400, type_: str = "invalid_request_error",
                 code: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.type = type_
        self.code = code
        self.headers = headers or {}

    def payload(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}


@dataclass
class ApiContext:
    """Tudo que uma rota autenticada da API precisa saber sobre a chamada."""
    user: User
    key: ApiKey
    db: AsyncSession
    ip: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def require(self, scope: str) -> None:
        if not keys_service.has_scope(self.key, scope):
            raise ApiError(
                f"Esta chave não tem a permissão '{scope}'.",
                status=403, type_="permission_error", code="insufficient_scope",
            )


def _bearer(request: Request) -> str:
    """Token do header Authorization. Aceita também `X-API-Key`, porque alguns
    clientes (n8n, planilhas, webhooks de terceiros) não deixam customizar o
    Authorization."""
    raw = request.headers.get("authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


def client_ip(request: Request) -> str:
    from ..config import get_settings

    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


_INVALID = ApiError(
    "Chave de API inválida.", status=401, type_="authentication_error",
    code="invalid_api_key",
)


async def require_api_key(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ApiContext:
    raw = _bearer(request)
    if not raw:
        raise ApiError(
            "Faltou a chave de API. Envie o header 'Authorization: Bearer <sua-chave>'.",
            status=401, type_="authentication_error", code="missing_api_key",
        )
    parsed = keys_service.parse(raw)
    if parsed is None:
        raise _INVALID
    prefix, secret = parsed

    key = await db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    # a comparação do hash roda mesmo sem a linha existir? não: com prefixo
    # desconhecido não há hash a comparar, e o prefixo não é secreto — vazar
    # "existe/não existe" para um prefixo não dá vantagem a quem ataca, já que o
    # segredo tem 256 bits de entropia.
    if key is None or not keys_service.verify(secret, key.key_hash):
        raise _INVALID

    state = keys_service.key_state(key)
    if state != "active":
        msg = {
            "disabled": "Esta chave de API está desativada.",
            "revoked": "Esta chave de API foi revogada.",
            "expired": "Esta chave de API expirou.",
        }[state]
        raise ApiError(msg, status=401, type_="authentication_error", code=f"key_{state}")

    ip = client_ip(request)
    if not keys_service.ip_allowed(key, ip):
        raise ApiError(
            "IP de origem não autorizado para esta chave.",
            status=403, type_="permission_error", code="ip_not_allowed",
        )

    user = await db.get(User, key.user_id)
    if user is None or not user.is_active or user.status != "active":
        raise ApiError(
            "A conta dona desta chave está inativa.",
            status=403, type_="permission_error", code="account_inactive",
        )

    try:
        limits.check_rate(key)
    except limits.LimitError as exc:
        raise _limit_error(exc) from None

    key.last_used_at = datetime.now(timezone.utc)
    key.last_used_ip = ip[:64]
    await db.commit()

    return ApiContext(user=user, key=key, db=db, ip=ip)


def _limit_error(exc: limits.LimitError) -> ApiError:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
    return ApiError(
        exc.message, status=exc.status,
        type_="insufficient_quota" if exc.status == 402 else "rate_limit_error",
        code=exc.code, headers=headers,
    )


async def enforce_quotas(ctx: ApiContext) -> None:
    """Cotas persistentes — chamado só nas rotas que consomem modelo."""
    try:
        ctx.usage = await limits.check_quotas(ctx.db, ctx.key)
    except limits.LimitError as exc:
        from . import webhooks

        webhooks.emit(ctx.key, "limit.reached", {"code": exc.code, "message": exc.message})
        raise _limit_error(exc) from None


def scoped(scope: str):
    """Dependency que exige um escopo: `Depends(scoped("memory:write"))`."""

    async def _dep(ctx: ApiContext = Depends(require_api_key)) -> ApiContext:
        ctx.require(scope)
        return ctx

    return _dep
