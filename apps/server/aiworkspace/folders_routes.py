"""Pastas para organizar chats: CRUD."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import Folder, User
from .schemas.chat import FolderCreate, FolderOut, FolderUpdate

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("", response_model=list[FolderOut])
async def list_folders(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Folder).where(Folder.user_id == user.id).order_by(Folder.name)
    )
    return list(rows)


@router.post("", response_model=FolderOut)
async def create_folder(
    body: FolderCreate, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    folder = Folder(user_id=user.id, name=body.name, parent_id=body.parent_id)
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return folder


async def _owned(db: AsyncSession, folder_id: uuid.UUID, user: User) -> Folder:
    folder = await db.get(Folder, folder_id)
    if folder is None or folder.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pasta não encontrada")
    return folder


@router.patch("/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: uuid.UUID,
    body: FolderUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    folder = await _owned(db, folder_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(folder, field, value)
    await db.commit()
    await db.refresh(folder)
    return folder


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    folder = await _owned(db, folder_id, user)
    await db.delete(folder)  # chats têm folder_id ON DELETE SET NULL
    await db.commit()
    return {"ok": True}
