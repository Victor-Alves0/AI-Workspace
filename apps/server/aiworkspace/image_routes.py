"""Serve imagens geradas pela IA. A URL é assinada (token `t` no query) — funciona
em <img src> sem depender de cookie cross-origin. O token só codifica o id (URL-
capacidade, imagem não é dado sensível)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import GeneratedImage
from .providers import image_gen

router = APIRouter(tags=["images"])


@router.get("/images/{image_id}")
async def get_image(
    image_id: uuid.UUID, t: str = "", db: AsyncSession = Depends(get_db)
):
    if not image_gen.verify_image_token(str(image_id), t):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")
    row = await db.get(GeneratedImage, image_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Imagem não encontrada")
    return Response(
        content=row.data,
        media_type=row.mime or "image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
