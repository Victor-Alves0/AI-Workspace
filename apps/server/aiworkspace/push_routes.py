"""Rotas do Web Push: chave pública VAPID, inscrever/desinscrever, teste."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import push_service
from .auth.deps import require_approved
from .db import get_db
from .models import PushSubscription, User

router = APIRouter(prefix="/push", tags=["push"])


class SubIn(BaseModel):
    endpoint: str
    keys: dict[str, str] = {}
    ua: str = ""


@router.get("/vapid")
async def vapid_key():
    """Chave pública VAPID (base64url) — o front usa como applicationServerKey."""
    return {"public_key": await push_service.get_public_key()}


@router.get("/status")
async def push_status(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    n = len(list(await db.scalars(select(PushSubscription.id).where(PushSubscription.user_id == user.id))))
    return {"subscriptions": n}


@router.post("/subscribe")
async def subscribe(body: SubIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    if not body.endpoint:
        return {"ok": False}
    existing = await db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if existing is not None:
        existing.user_id = user.id
        existing.p256dh = body.keys.get("p256dh", "")
        existing.auth = body.keys.get("auth", "")
        existing.ua = body.ua[:255]
    else:
        db.add(PushSubscription(
            user_id=user.id, endpoint=body.endpoint,
            p256dh=body.keys.get("p256dh", ""), auth=body.keys.get("auth", ""), ua=body.ua[:255],
        ))
    await db.commit()
    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(body: SubIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user.id, PushSubscription.endpoint == body.endpoint
        )
    )
    await db.commit()
    return {"ok": True}


@router.post("/test")
async def test_push(user: User = Depends(require_approved)):
    sent = await push_service.send_to_user(user.id, "Singularity AI", "Notificação de teste 🔔", "/")
    return {"ok": sent > 0, "sent": sent}
