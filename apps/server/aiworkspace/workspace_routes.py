"""Seletor de pastas do chat: as pastas do usuário, navegação pelo disco e criar/usar
uma pasta.

Cada chat trabalha numa pasta (`chats.workspace`): a principal por padrão, outra
escolhida aqui, ou nenhuma. A pasta vira um projeto do Codespace (source='folder'),
então os arquivos que a IA grava aparecem lá e no disco, onde a pessoa escolheu.

Quem pode escolher o quê: o ADMIN (dono da instância — no app desktop, a própria
pessoa) navega e escolhe qualquer pasta do computador/servidor. Os demais usuários
ficam dentro da própria pasta principal — senão um usuário leria o disco do servidor.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .codespace import graph_service
from .db import get_db
from .models import CodespaceProject, User

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _is_admin(user: User) -> bool:
    return getattr(user, "role", "") == "admin"


def _anywhere(user: User) -> bool:
    return _is_admin(user) and graph_service.folders_anywhere()


def _allowed(user: User, path: str | Path) -> bool:
    if _anywhere(user):
        return True
    if _is_admin(user):  # servidor: o admin navega a área de projetos inteira
        return graph_service.within_data_root(path)
    return graph_service.within_home(str(user.id), path)


def _folder_out(p: CodespaceProject) -> dict:
    path = p.local_path or str(graph_service.working_copy_path(str(p.user_id), str(p.id)))
    return {"id": str(p.id), "name": p.name, "path": path, "source": p.source,
            "home": bool((p.scope or {}).get("home"))}


@router.get("/folders")
async def list_folders(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    """A pasta principal + os projetos/pastas do usuário (para o seletor)."""
    home = await graph_service.ensure_home_project(db, user.id)
    rows = list(await db.scalars(
        select(CodespaceProject).where(CodespaceProject.user_id == user.id)
        .order_by(CodespaceProject.updated_at.desc())
    ))
    return {
        "home": _folder_out(home),
        "folders": [_folder_out(p) for p in rows if p.id != home.id],
        "browse_anywhere": _anywhere(user),
    }


def _roots() -> list[dict]:
    if os.name == "nt":
        return [{"name": f"{d}:\\", "path": f"{d}:\\"} for d in string.ascii_uppercase
                if os.path.isdir(f"{d}:\\")]
    return [{"name": "/", "path": "/"}]


@router.get("/browse")
async def browse(
    path: str | None = Query(None, max_length=1000),
    hidden: bool = False,
    user: User = Depends(require_approved),
):
    """Subpastas de `path` (padrão: a pasta principal). Só diretórios."""
    home = graph_service.workspace_home(str(user.id))
    alvo = Path(path).expanduser() if path else home
    try:
        alvo = alvo.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "caminho inválido") from None
    if not _allowed(user, alvo):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "fora da sua pasta principal")
    if not alvo.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pasta não encontrada")
    dirs = []
    try:
        with os.scandir(alvo) as it:
            for e in it:
                if not hidden and e.name.startswith((".", "$")):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        dirs.append({"name": e.name, "path": str(alvo / e.name)})
                except OSError:
                    continue
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sem permissão para ler esta pasta") from None
    dirs.sort(key=lambda d: d["name"].lower())
    parent = str(alvo.parent) if alvo.parent != alvo else None
    if parent and not _allowed(user, parent):
        parent = None
    return {
        "path": str(alvo), "parent": parent, "home": str(home), "dirs": dirs[:500],
        "truncated": len(dirs) > 500,
        "roots": (_roots() if _anywhere(user)
                  else [{"name": "Projetos", "path": str(graph_service.data_root().resolve())}] if _is_admin(user)
                  else [{"name": "Pasta principal", "path": str(home)}]),
    }


class FolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=255)
    # cria a pasta se não existir (ex.: "Nova pasta" no seletor, ou o pedido da IA)
    create: bool = False


@router.post("/folders")
async def use_folder(body: FolderIn, user: User = Depends(require_approved),
                     db: AsyncSession = Depends(get_db)):
    """Registra a pasta (criando-a, se pedido) e devolve o id para o chat usar."""
    alvo = Path(body.path).expanduser()
    if not alvo.is_absolute():
        alvo = graph_service.workspace_home(str(user.id)) / alvo
    try:
        alvo = alvo.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "caminho inválido") from None
    if not _allowed(user, alvo):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "fora da sua pasta principal")
    if not alvo.exists():
        if not body.create:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "pasta não encontrada")
        try:
            alvo.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"não consegui criar a pasta: {exc}") from None
    if not alvo.is_dir():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "não é uma pasta")
    if graph_service.within_home(str(user.id), alvo) and alvo == graph_service.workspace_home(str(user.id)).resolve():
        p = await graph_service.ensure_home_project(db, user.id)
    else:
        p = await graph_service.folder_project(db, user.id, str(alvo), body.name)
    return _folder_out(p)
