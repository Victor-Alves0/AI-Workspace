"""CRUD das chaves de API — usado pelo painel (autenticação por cookie).

Fica separado de `/v1` de propósito: aqui quem fala é o DONO da conta pelo
navegador, não uma aplicação integrada. Uma chave de API nunca administra outras
chaves — senão uma credencial vazada poderia criar credenciais novas e sobreviver
à revogação.

O segredo em claro aparece uma única vez, na resposta do POST. Depois disso não
existe mais em lugar nenhum: "regenerar" cria um segredo novo na MESMA linha,
preservando nome, limites e histórico.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit_service
from ..auth.deps import require_approved
from ..db import get_db
from ..models import ApiKey, ApiRequest, ModelConfig, User
from . import keys_service, limits, webhooks

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class KeyIn(BaseModel):
    name: str = ""
    scopes: list[str] = Field(default_factory=lambda: list(keys_service.DEFAULT_SCOPES))
    model_policy: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    ip_allowlist: list[str] = Field(default_factory=list)
    webhook: dict[str, Any] = Field(default_factory=dict)
    # tempo de vida em dias (TTL). Vence junto com `expires_at`, se ambos vierem
    # vale o mais próximo — o que expira primeiro é o mais restritivo.
    ttl_days: int | None = None
    expires_at: datetime | None = None


class KeyUpdate(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    model_policy: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None
    ip_allowlist: list[str] | None = None
    webhook: dict[str, Any] | None = None
    enabled: bool | None = None
    expires_at: datetime | None = None
    ttl_days: int | None = None


_LIMIT_FIELDS = ("rpm", "rpd", "monthly_requests", "tokens_in", "tokens_out",
                 "concurrency")


def _clean_scopes(raw: list[str] | None) -> list[str]:
    scopes = [s for s in (raw or []) if s in keys_service.ALL_SCOPES]
    return scopes or list(keys_service.DEFAULT_SCOPES)


def _clean_limits(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Números não-negativos; 0 significa "sem limite" e sai do dicionário."""
    out: dict[str, Any] = {}
    for f in _LIMIT_FIELDS:
        try:
            v = int(raw.get(f) or 0) if raw else 0
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out[f] = v
    try:
        budget = float((raw or {}).get("budget_usd") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    if budget > 0:
        out["budget_usd"] = round(budget, 4)
    return out


def _clean_memory(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    mode = raw.get("mode")
    out: dict[str, Any] = {"mode": mode if mode in keys_service.MEMORY_MODES else "none"}
    for f in ("ttl_days", "max_items"):
        try:
            v = int(raw.get(f) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out[f] = v
    return out


def _clean_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    mode = "allow" if raw.get("mode") == "allow" else "all"
    out: dict[str, Any] = {"mode": mode}
    ids = [str(i) for i in (raw.get("ids") or []) if str(i).strip()]
    if mode == "allow":
        out["ids"] = ids[:200]
    if raw.get("default"):
        out["default"] = str(raw["default"])
    return out


def _clean_webhook(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    url = str(raw.get("url") or "").strip()
    if not url:
        return {}
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "URL do webhook precisa começar com http:// ou https://")
    events = [e for e in (raw.get("events") or []) if e in webhooks.EVENTS]
    return {"url": url[:500], "secret": str(raw.get("secret") or "")[:200],
            "events": events}


def _expiry(ttl_days: int | None, expires_at: datetime | None) -> datetime | None:
    """Menor entre o TTL e a data absoluta — o mais restritivo vence."""
    candidates = []
    if ttl_days and ttl_days > 0:
        candidates.append(datetime.now(timezone.utc) + timedelta(days=int(ttl_days)))
    if expires_at is not None:
        dt = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        candidates.append(dt)
    return min(candidates) if candidates else None


def _out(key: ApiKey, *, cost_month: float = 0.0, requests: int = 0) -> dict[str, Any]:
    return {
        "id": str(key.id),
        "name": key.name,
        "masked": keys_service.masked(key.prefix),
        "state": keys_service.key_state(key),
        "scopes": list(key.scopes or []),
        "model_policy": key.model_policy or {"mode": "all"},
        "limits": key.limits or {},
        "memory": key.memory or {"mode": "none"},
        "ip_allowlist": list(key.ip_allowlist or []),
        # o segredo do webhook não volta ao cliente; só dizemos se existe
        "webhook": {
            "url": (key.webhook or {}).get("url") or "",
            "events": (key.webhook or {}).get("events") or [],
            "has_secret": bool((key.webhook or {}).get("secret")),
        },
        "enabled": bool(key.enabled),
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "last_used_ip": key.last_used_ip or "",
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "cost_month": round(cost_month, 6),
        "requests_month": requests,
    }


async def _owned(db: AsyncSession, user: User, key_id: uuid.UUID) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chave não encontrada")
    return key


@router.get("")
async def list_keys(user: User = Depends(require_approved),
                    db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(desc(ApiKey.created_at))
    ))
    costs = await limits.month_cost_by_key(db, user.id)
    month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    counts = dict((await db.execute(
        select(ApiRequest.api_key_id, func.count())
        .where(ApiRequest.user_id == user.id, ApiRequest.created_at >= month)
        .group_by(ApiRequest.api_key_id)
    )).all())
    return [
        _out(k, cost_month=costs.get(str(k.id), 0.0), requests=int(counts.get(k.id, 0)))
        for k in rows
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_key(body: KeyIn, user: User = Depends(require_approved),
                     db: AsyncSession = Depends(get_db)):
    plain, prefix, key_hash = keys_service.generate()
    key = ApiKey(
        user_id=user.id,
        name=(body.name or "Chave sem nome")[:120],
        prefix=prefix,
        key_hash=key_hash,
        scopes=_clean_scopes(body.scopes),
        model_policy=_clean_policy(body.model_policy),
        limits=_clean_limits(body.limits),
        memory=_clean_memory(body.memory),
        ip_allowlist=[str(i)[:64] for i in (body.ip_allowlist or [])][:50],
        webhook=_clean_webhook(body.webhook),
        expires_at=_expiry(body.ttl_days, body.expires_at),
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    await audit_service.record("api_key_created", user_id=user.id,
                              detail={"key_id": str(key.id), "name": key.name})
    webhooks.emit(key, "key.created", {"name": key.name})
    # única vez que o segredo existe fora do cliente
    return {**_out(key), "key": plain}


@router.patch("/{key_id}")
async def update_key(key_id: uuid.UUID, body: KeyUpdate,
                     user: User = Depends(require_approved),
                     db: AsyncSession = Depends(get_db)):
    key = await _owned(db, user, key_id)
    if body.name is not None:
        key.name = body.name[:120]
    if body.scopes is not None:
        key.scopes = _clean_scopes(body.scopes)
    if body.model_policy is not None:
        key.model_policy = _clean_policy(body.model_policy)
    if body.limits is not None:
        key.limits = _clean_limits(body.limits)
        key.alerts_sent = []  # teto novo: os marcos de alerta valem de novo
    if body.memory is not None:
        key.memory = _clean_memory(body.memory)
    if body.ip_allowlist is not None:
        key.ip_allowlist = [str(i)[:64] for i in body.ip_allowlist][:50]
    if body.webhook is not None:
        cleaned = _clean_webhook(body.webhook)
        # segredo vazio no PATCH = "mantém o que está lá", não "apaga": o painel
        # nunca recebe o segredo de volta, então não teria como reenviá-lo
        if cleaned and not cleaned.get("secret"):
            cleaned["secret"] = (key.webhook or {}).get("secret", "")
        key.webhook = cleaned
    if body.enabled is not None:
        key.enabled = bool(body.enabled)
    if body.expires_at is not None or body.ttl_days is not None:
        key.expires_at = _expiry(body.ttl_days, body.expires_at)
    await db.commit()
    await db.refresh(key)
    return _out(key)


@router.post("/{key_id}/revoke")
async def revoke_key(key_id: uuid.UUID, user: User = Depends(require_approved),
                     db: AsyncSession = Depends(get_db)):
    """Revogação é IRREVERSÍVEL e vale na hora: a próxima requisição já falha."""
    key = await _owned(db, user, key_id)
    key.revoked_at = datetime.now(timezone.utc)
    key.enabled = False
    await db.commit()
    await db.refresh(key)
    await audit_service.record("api_key_revoked", user_id=user.id,
                              detail={"key_id": str(key.id), "name": key.name})
    webhooks.emit(key, "key.revoked", {"name": key.name})
    return _out(key)


@router.post("/{key_id}/regenerate")
async def regenerate_key(key_id: uuid.UUID, user: User = Depends(require_approved),
                         db: AsyncSession = Depends(get_db)):
    """Troca o segredo mantendo a configuração — o caminho para chave comprometida
    sem ter que recriar limites, permissões e webhook do zero."""
    key = await _owned(db, user, key_id)
    if key.revoked_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Chave revogada não pode ser regenerada; crie uma nova.")
    plain, prefix, key_hash = keys_service.generate()
    key.prefix = prefix
    key.key_hash = key_hash
    key.enabled = True
    await db.commit()
    await db.refresh(key)
    await audit_service.record("api_key_regenerated", user_id=user.id,
                              detail={"key_id": str(key.id)})
    return {**_out(key), "key": plain}


@router.delete("/{key_id}")
async def delete_key(key_id: uuid.UUID, user: User = Depends(require_approved),
                     db: AsyncSession = Depends(get_db)):
    key = await _owned(db, user, key_id)
    await db.delete(key)
    await db.commit()
    await audit_service.record("api_key_deleted", user_id=user.id,
                              detail={"key_id": str(key_id)})
    return {"ok": True}


@router.get("/{key_id}/requests")
async def key_requests(
    key_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    only_errors: bool = False,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Histórico de requisições da chave — o que se olha quando algo quebrou."""
    await _owned(db, user, key_id)
    stmt = select(ApiRequest).where(ApiRequest.api_key_id == key_id)
    if only_errors:
        stmt = stmt.where(ApiRequest.status >= 400)
    rows = list(await db.scalars(stmt.order_by(desc(ApiRequest.created_at)).limit(limit)))
    return [
        {
            "id": str(r.id), "endpoint": r.endpoint, "model": r.model,
            "status": r.status, "error": r.error, "latency_ms": r.latency_ms,
            "total_tokens": r.total_tokens, "cost": round(r.cost, 6), "ip": r.ip,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/meta")
async def meta(user: User = Depends(require_approved),
               db: AsyncSession = Depends(get_db)):
    """Opções para montar o formulário: escopos, eventos, modos e os modelos que
    podem ser liberados numa chave."""
    rows = list(await db.scalars(
        select(ModelConfig)
        .where(ModelConfig.user_id == user.id, ModelConfig.enabled.is_(True))
        .order_by(ModelConfig.name)
    ))
    return {
        "scopes": list(keys_service.ALL_SCOPES),
        "default_scopes": list(keys_service.DEFAULT_SCOPES),
        "memory_modes": list(keys_service.MEMORY_MODES),
        "webhook_events": list(webhooks.EVENTS),
        "models": [
            {"id": m.slug or str(m.id), "name": m.name, "base_model": m.base_model}
            for m in rows
        ],
    }
