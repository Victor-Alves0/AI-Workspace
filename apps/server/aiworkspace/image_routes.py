"""Serve mídia gerada pela IA com escopo do dono ou de um chat compartilhado."""

from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import optional_approved_user
from .db import get_db
from .knowledge_routes import _parse_range
from .models import GeneratedImage, User
from .providers import image_gen
from .shared_media import shared_chat_allows

router = APIRouter(tags=["images"])


@router.get("/images/{image_id}")
async def get_image(
    image_id: uuid.UUID, request: Request, t: str = "", s: str = "", download: bool = False,
    user: User | None = Depends(optional_approved_user),
    db: AsyncSession = Depends(get_db),
):
    private_token = image_gen.verify_image_token(str(image_id), t)
    shared_public_id = image_gen.verify_shared_image_token(str(image_id), s)
    if not private_token and not shared_public_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")
    row = await db.get(GeneratedImage, image_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Imagem não encontrada")
    owner_access = bool(private_token and user is not None and row.user_id == user.id)
    share_access = await shared_chat_allows(
        db, chat_id=row.chat_id, owner_id=row.user_id, public_id=shared_public_id
    )
    if not owner_access and not share_access:
        # 404 não revela para quem tem um id/token de outro usuário que a mídia existe.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Imagem não encontrada")
    blob = bytes(row.data)
    mime = row.mime or "image/png"
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Accept-Ranges": "bytes",
    }
    if download:
        ext = mimetypes.guess_extension(mime) or ".png"
        headers["Content-Disposition"] = f'attachment; filename="ai-workspace-{image_id}{ext}"'
    rng = _parse_range(request.headers.get("range", ""), len(blob))
    if rng is not None:
        start, end = rng
        return Response(
            content=blob[start : end + 1],
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=mime,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{len(blob)}"},
        )
    return Response(
        content=blob,
        media_type=mime,
        headers=headers,
    )
