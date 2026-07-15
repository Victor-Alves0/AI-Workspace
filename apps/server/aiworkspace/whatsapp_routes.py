"""Rotas da integração WhatsApp.

CRUD das conexões (números) + fluxo de conexão (QR do Evolution / credenciais
do Cloud API) + webhooks públicos. Os webhooks NÃO passam por login: a URL
carrega um token aleatório próprio (e o Cloud API ainda valida a assinatura
X-Hub-Signature-256 quando o app secret está configurado).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .config import get_settings
from .db import get_db
from .integrations import whatsapp_evolution as evolution
from .integrations import whatsapp_official as official
from .integrations import whatsapp_service
from .models import User, WhatsAppConnection, WhatsAppThread

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp"])

_DEFAULT_FILTERS = {"policy": "all", "allow": [], "block": [], "groups": False, "trigger": ""}


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ConnectionIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    label: str = Field(default="", max_length=120)
    provider: str = Field(default="evolution", pattern=r"^(evolution|official)$")
    model_config_id: uuid.UUID | None = None
    model: str = Field(default="", max_length=255)
    # Cloud API (provider "official")
    phone: str = Field(default="", max_length=32)
    phone_number_id: str = Field(default="", max_length=64)
    access_token: str = Field(default="", max_length=1024)
    app_secret: str = Field(default="", max_length=256)


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    label: str | None = Field(default=None, max_length=120)
    model_config_id: uuid.UUID | None = None
    clear_model_config: bool = False  # PATCH não distingue "ausente" de null
    model: str | None = Field(default=None, max_length=255)
    filters: dict[str, Any] | None = None
    memory: str | None = Field(default=None, pattern=r"^(local|global)$")
    enabled: bool | None = None
    phone_number_id: str | None = Field(default=None, max_length=64)
    access_token: str | None = Field(default=None, max_length=1024)  # vazio = mantém
    app_secret: str | None = Field(default=None, max_length=256)
    # prompt adicional do número (concatenado ao system prompt do modelo)
    system_prompt: str | None = Field(default=None, max_length=8000)
    # limites de mensagens por contato: {total, per_hour, per_day, per_month}
    limits: dict[str, Any] | None = None
    # contexto/roles por número: [{number, name, role, context}]
    contacts: list[dict[str, Any]] | None = None
    # Modo humanizador: {enabled, typing, min_seconds, max_seconds, split}
    humanize: dict[str, Any] | None = None
    # janela de silêncio (s) p/ juntar mensagens fragmentadas num único turno (0 = off)
    debounce_seconds: int | None = Field(default=None, ge=0, le=60)


_LIMIT_KEYS = ("total", "per_hour", "per_day", "per_month")


def _clean_limits(raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in _LIMIT_KEYS:
        try:
            v = int(raw.get(k) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out[k] = v
    return out


def _clean_humanize(raw: dict[str, Any]) -> dict[str, Any]:
    def _sec(key: str, default: int) -> int:
        try:
            return max(0, min(int(raw.get(key, default)), 120))
        except (TypeError, ValueError):
            return default
    return {
        "enabled": bool(raw.get("enabled")),
        "typing": raw.get("typing", True) is not False,
        "split": bool(raw.get("split")),
        "min_seconds": _sec("min_seconds", 1),
        "max_seconds": _sec("max_seconds", 6),
    }


def _clean_contacts(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for c in raw[:200]:
        number = str(c.get("number") or "").strip()[:40]
        if not number:
            continue
        out.append({
            "number": number,
            "name": str(c.get("name") or "").strip()[:120],
            "role": str(c.get("role") or "").strip()[:120],
            "context": str(c.get("context") or "").strip()[:2000],
        })
    return out


def _serialize(conn: WhatsAppConnection, threads: int = 0) -> dict[str, Any]:
    return {
        "id": str(conn.id),
        "label": conn.label,
        "provider": conn.provider,
        "phone": conn.phone,
        "model_config_id": str(conn.model_config_id) if conn.model_config_id else None,
        "model": conn.model,
        "filters": {**_DEFAULT_FILTERS, **(conn.filters or {})},
        "memory": conn.memory,
        "system_prompt": conn.system_prompt or "",
        "limits": conn.limits or {},
        "contacts": conn.contacts or [],
        "humanize": conn.humanize or {},
        "debounce_seconds": conn.debounce_seconds or 0,
        "enabled": conn.enabled,
        "state": conn.state or {},
        "threads": threads,
        "phone_number_id": conn.phone_number_id,
        "has_token": bool(conn.access_token),
        "verify_token": conn.verify_token,
        # a UI monta a URL completa com a base pública do server
        "webhook_path": f"/integrations/whatsapp/webhook/{conn.provider}/{conn.webhook_token}",
        "created_at": conn.created_at.isoformat(),
    }


async def _owned(db: AsyncSession, connection_id: uuid.UUID, user: User) -> WhatsAppConnection:
    conn = await db.get(WhatsAppConnection, connection_id)
    if conn is None or conn.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conexão não encontrada")
    return conn


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.get("")
async def list_connections(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    rows = list(await db.scalars(
        select(WhatsAppConnection)
        .where(WhatsAppConnection.user_id == user.id)
        .order_by(WhatsAppConnection.created_at)
    ))
    counts: dict[uuid.UUID, int] = {}
    if rows:
        pairs = await db.execute(
            select(WhatsAppThread.connection_id, func.count())
            .where(WhatsAppThread.connection_id.in_([r.id for r in rows]))
            .group_by(WhatsAppThread.connection_id)
        )
        counts = {cid: n for cid, n in pairs}
    return {
        "evolution_available": evolution.configured(),
        "connections": [_serialize(c, counts.get(c.id, 0)) for c in rows],
    }


@router.post("")
async def create_connection(
    body: ConnectionIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    webhook_token = secrets.token_urlsafe(24)
    conn = WhatsAppConnection(
        user_id=user.id,
        label=body.label.strip(),
        provider=body.provider,
        model_config_id=body.model_config_id,
        model=body.model.strip(),
        filters=dict(_DEFAULT_FILTERS),
        webhook_token=webhook_token,
    )

    if body.provider == "evolution":
        if not evolution.configured():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Evolution API não habilitada neste deploy. Suba o serviço com "
                "`docker compose --profile whatsapp up -d` e defina EVOLUTION_API_KEY no .env.",
            )
        conn.instance = f"aw{uuid.uuid4().hex[:10]}"
        webhook_url = (
            settings.whatsapp_webhook_base.rstrip("/")
            + f"/integrations/whatsapp/webhook/evolution/{webhook_token}"
        )
        try:
            await evolution.create_instance(conn.instance, webhook_url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Falha ao criar a instância no Evolution: {exc}",
            )
        conn.state = {"status": "connecting"}
    else:  # official
        if not body.phone_number_id.strip() or not body.access_token.strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Informe o Phone Number ID e o Access Token do Cloud API",
            )
        conn.phone = body.phone.strip()
        conn.phone_number_id = body.phone_number_id.strip()
        conn.access_token = body.access_token.strip()
        conn.app_secret = body.app_secret.strip()
        conn.verify_token = secrets.token_urlsafe(16)
        conn.state = {"status": "configured"}

    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return _serialize(conn)


@router.patch("/{connection_id}")
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    conn = await _owned(db, connection_id, user)
    data = body.model_dump(exclude_unset=True)
    data.pop("clear_model_config", None)
    if body.clear_model_config:
        conn.model_config_id = None
        data.pop("model_config_id", None)
    if "access_token" in data and not (data["access_token"] or "").strip():
        data.pop("access_token")  # vazio = mantém o atual
    if "app_secret" in data and data["app_secret"] is None:
        data.pop("app_secret")
    if "limits" in data:
        data["limits"] = _clean_limits(data["limits"] or {})
    if "contacts" in data:
        data["contacts"] = _clean_contacts(data["contacts"] or [])
    if "humanize" in data:
        data["humanize"] = _clean_humanize(data["humanize"] or {})
    for field, value in data.items():
        setattr(conn, field, value)
    await db.commit()
    await db.refresh(conn)
    return _serialize(conn)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    conn = await _owned(db, connection_id, user)
    if conn.provider == "evolution" and conn.instance and evolution.configured():
        await evolution.delete_instance(conn.instance)
    # os Chats das conversas ficam (histórico do usuário); só o vínculo se desfaz
    await db.delete(conn)
    await db.commit()


# --------------------------------------------------------------------------- #
# Fluxo de conexão (Evolution)
# --------------------------------------------------------------------------- #
@router.get("/{connection_id}/qr")
async def get_qr(
    connection_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    conn = await _owned(db, connection_id, user)
    if conn.provider != "evolution":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Esta conexão não usa QR Code")
    try:
        data = await evolution.get_qr(conn.instance)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao obter o QR: {exc}")
    return {"base64": data.get("base64") or "", "code": data.get("code") or ""}


@router.get("/{connection_id}/status")
async def get_status(
    connection_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    conn = await _owned(db, connection_id, user)
    if conn.provider != "evolution":
        return {"status": (conn.state or {}).get("status", "configured"), "phone": conn.phone}
    try:
        state = await evolution.get_state(conn.instance)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "phone": conn.phone, "error": str(exc)}
    if state == "open" and not conn.phone:
        profile = await evolution.get_profile(conn.instance)
        conn.phone = profile.get("phone") or ""
        if profile.get("name") and not conn.label:
            conn.label = profile["name"]
    conn.state = {**(conn.state or {}), "status": state}
    await db.commit()
    return {"status": state, "phone": conn.phone}


# --------------------------------------------------------------------------- #
# Webhooks (públicos — identificados pelo token da URL)
# --------------------------------------------------------------------------- #
async def _conn_by_token(db: AsyncSession, provider: str, token: str) -> WhatsAppConnection | None:
    if not token:
        return None
    return await db.scalar(
        select(WhatsAppConnection).where(
            WhatsAppConnection.webhook_token == token,
            WhatsAppConnection.provider == provider,
        )
    )


@router.post("/webhook/evolution/{token}")
async def evolution_webhook(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    conn = await _conn_by_token(db, "evolution", token)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {"ok": False}
    event = (payload.get("event") or "").replace(".", "_").lower()
    if event == "connection_update":
        state = (payload.get("data") or {}).get("state") or ""
        if state:
            conn.state = {**(conn.state or {}), "status": state}
            await db.commit()
        return {"ok": True}
    messages = evolution.parse_webhook(payload)
    if messages:
        # responde 200 já; o processamento (modelo + resposta) segue em background
        asyncio.create_task(whatsapp_service.handle_incoming(conn.id, messages))
    return {"ok": True}


@router.get("/webhook/official/{token}")
async def official_webhook_verify(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Verificação do webhook (Meta chama com hub.challenge ao registrar a URL)."""
    conn = await _conn_by_token(db, "official", token)
    params = request.query_params
    if (
        conn is not None
        and params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == conn.verify_token
    ):
        return PlainTextResponse(params.get("hub.challenge") or "")
    raise HTTPException(status.HTTP_403_FORBIDDEN)


@router.post("/webhook/official/{token}")
async def official_webhook(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    conn = await _conn_by_token(db, "official", token)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    body = await request.body()
    if not official.valid_signature(
        conn.app_secret, body, request.headers.get("x-hub-signature-256")
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Assinatura inválida")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False}
    messages = official.parse_webhook(payload)
    if messages:
        asyncio.create_task(whatsapp_service.handle_incoming(conn.id, messages))
    return {"ok": True}
