"""Web Push (notificações do navegador) via VAPID + pywebpush.

As chaves VAPID são geradas UMA vez e guardadas em app_settings ("webpush_vapid").
O front pega a chave pública (`/push/vapid`) p/ inscrever o navegador; o servidor
envia com a chave privada. Usado ao criar Notificações (automações). pywebpush é
síncrono (requests) → chamado via run_in_threadpool.
"""

from __future__ import annotations

import base64
import json
import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, select

from .app_config import get_setting, set_setting
from .db import SessionLocal
from .models import PushSubscription

logger = logging.getLogger(__name__)

_VAPID_KEY = "webpush_vapid"
_CLAIM_SUB = "mailto:noreply@aiworkspace.app"


def _generate_vapid() -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    pub_b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"private_pem": pem, "public_b64": pub_b64}


async def _ensure_vapid(db) -> dict[str, str]:
    cfg = await get_setting(db, _VAPID_KEY)
    if isinstance(cfg, dict) and cfg.get("private_pem") and cfg.get("public_b64"):
        return cfg
    cfg = _generate_vapid()
    await set_setting(db, _VAPID_KEY, cfg)
    logger.info("VAPID gerado (Web Push habilitado)")
    return cfg


async def get_public_key() -> str:
    """Chave pública VAPID (base64url) p/ o `applicationServerKey` do navegador."""
    async with SessionLocal() as db:
        cfg = await _ensure_vapid(db)
        return cfg["public_b64"]


def _push_one(sub: dict, payload: str, private_pem: str) -> int:
    """Envia a UM endpoint (bloqueante). Retorna o status HTTP (0 em erro de rede)."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=private_pem,
            vapid_claims={"sub": _CLAIM_SUB},
            timeout=10,
        )
        return 201
    except WebPushException as exc:
        return getattr(exc.response, "status_code", 0) or 0


async def send_to_user(user_id, title: str, body: str, url: str = "/") -> int:
    """Envia uma notificação push a TODOS os dispositivos inscritos do usuário.
    Poda inscrições mortas (404/410). Best-effort (nunca levanta). Retorna nº enviado."""
    payload = json.dumps({"title": title or "Singularity AI", "body": (body or "")[:400], "url": url or "/"})
    sent = 0
    try:
        async with SessionLocal() as db:
            cfg = await _ensure_vapid(db)
            subs = list(await db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))
            if not subs:
                return 0
            dead: list[str] = []
            for s in subs:
                info = {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}
                code = await run_in_threadpool(_push_one, info, payload, cfg["private_pem"])
                if code in (404, 410):
                    dead.append(s.endpoint)
                elif code and code < 400:
                    sent += 1
            if dead:
                await db.execute(delete(PushSubscription).where(PushSubscription.endpoint.in_(dead)))
                await db.commit()
    except Exception:  # noqa: BLE001 - push é best-effort
        logger.exception("falha ao enviar web push (user %s)", user_id)
    return sent
