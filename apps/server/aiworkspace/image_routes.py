"""Serve mídia gerada pela IA (imagens e vídeos — a tabela nasceu p/ imagens e o
nome ficou). A URL é assinada (token `t` no query) — funciona em <img>/<video>
sem depender de cookie cross-origin. O token só codifica o id (URL-capacidade,
mídia gerada não é dado sensível). Honra `Range` (206) para o <video> buscar
(seek) no player — imagem ignora o header e segue no caminho de sempre."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .knowledge_routes import _parse_range
from .models import GeneratedImage
from .providers import image_gen

router = APIRouter(tags=["images"])


@router.get("/images/{image_id}")
async def get_image(
    image_id: uuid.UUID, request: Request, t: str = "", db: AsyncSession = Depends(get_db)
):
    if not image_gen.verify_image_token(str(image_id), t):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")
    row = await db.get(GeneratedImage, image_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Imagem não encontrada")
    blob = bytes(row.data)
    mime = row.mime or "image/png"
    rng = _parse_range(request.headers.get("range", ""), len(blob))
    if rng is not None:
        start, end = rng
        return Response(
            content=blob[start : end + 1],
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=mime,
            headers={
                "Content-Range": f"bytes {start}-{end}/{len(blob)}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
    return Response(
        content=blob,
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Accept-Ranges": "bytes",
        },
    )
