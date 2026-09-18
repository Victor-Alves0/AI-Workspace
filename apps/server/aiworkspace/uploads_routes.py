"""Rotas dos anexos do chat: enviar, servir e apagar.

O envio é MULTIPART e vai direto para o disco em pedaços — é isto que permite um
arquivo de centenas de MB sem que o servidor o carregue inteiro na memória. O JSON do
envio da mensagem passa a levar só a referência.
"""

from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import uploads_service as svc
from .auth.deps import optional_approved_user, require_approved
from .db import get_db
from .extraction import is_extractable
from .models import Upload, User
from .shared_media import shared_chat_allows

router = APIRouter(prefix="/uploads", tags=["uploads"])

_CHUNK = 1024 * 1024


async def _chunks(file: UploadFile):
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        yield chunk


@router.get("/limits")
async def limits(_user: User = Depends(require_approved)):
    """Tetos que o compositor usa para recusar antes de subir o arquivo."""
    from .config import get_settings

    s = get_settings()
    return {
        "max_bytes": s.upload_max_bytes,
        "image_max_bytes": s.upload_image_max_bytes,
        "audio_max_bytes": s.upload_audio_max_bytes,
        "max_per_message": s.upload_max_per_message,
        "quota_bytes": s.upload_quota_bytes,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_upload(
    file: UploadFile,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Recebe UM arquivo e devolve a referência usada no anexo da mensagem."""
    try:
        row = await svc.store_stream(
            db, user.id, file.filename or "arquivo", file.content_type or "", _chunks(file),
        )
    except svc.UploadError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc

    # documentos viram texto UMA vez, aqui: o turno (e cada regeneração) reusa o
    # resultado em vez de reprocessar o arquivo.
    if row.kind == "file" and is_extractable(row.filename, row.mime):
        await _extract_into(db, row)
    return svc.out(row)


async def _extract_into(db: AsyncSession, row: Upload) -> None:
    from starlette.concurrency import run_in_threadpool

    from .config import get_settings
    from .extraction import extract

    data = svc.read_bytes(row)
    if data is None:
        return
    try:
        text = await run_in_threadpool(extract, row.filename, row.mime, data)
    except Exception:  # noqa: BLE001 - anexo sem texto ainda é anexo (o nome vai ao modelo)
        return
    if text:
        row.text = text[: get_settings().upload_text_max_chars]
        await db.commit()


@router.get("/{upload_id}")
async def serve_upload(
    upload_id: uuid.UUID, request: Request, t: str = "", s: str = "", download: bool = False,
    user: User | None = Depends(optional_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve anexo do dono ou de um compartilhamento ainda válido."""
    private_token = svc.verify_token(str(upload_id), t)
    shared_public_id = svc.verify_shared_token(str(upload_id), s)
    if not private_token and not shared_public_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")
    row = await db.get(Upload, upload_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Arquivo não encontrado")
    owner_access = bool(private_token and user is not None and row.user_id == user.id)
    share_access = await shared_chat_allows(
        db, chat_id=row.chat_id, owner_id=row.user_id, public_id=shared_public_id
    )
    if not owner_access and not share_access:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Arquivo não encontrado")
    path = svc.file_path(row)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Arquivo não está mais no servidor")
    mime = row.mime or mimetypes.guess_type(row.filename)[0] or "application/octet-stream"
    headers = {"Cache-Control": "private, max-age=86400"}
    return FileResponse(
        path, media_type=mime, headers=headers,
        filename=row.filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(
    upload_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Upload, upload_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Arquivo não encontrado")
    svc.delete_file(row)
    await db.delete(row)
    await db.commit()


@router.get("")
async def list_uploads(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Arquivos do usuário + quanto da cota já foi usada (painel de armazenamento)."""
    rows = list(await db.scalars(
        select(Upload).where(Upload.user_id == user.id).order_by(Upload.created_at.desc()).limit(500)
    ))
    return {
        "items": [svc.out(row) | {"attached": row.attached} for row in rows],
        "used_bytes": await svc.used_bytes(db, user.id),
    }
