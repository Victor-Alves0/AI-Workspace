"""Painel do admin: gestão de usuários e configurações globais."""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__, network_config
from .app_config import ALLOW_SIGNUPS, get_setting, set_setting
from .auth.deps import require_admin
from .config import get_settings
from .db import get_db
from .models import User

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ConfigIn(BaseModel):
    allow_signups: bool


@router.get("/config")
async def get_config(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return {"allow_signups": bool(await get_setting(db, ALLOW_SIGNUPS, False))}


@router.put("/config")
async def put_config(
    body: ConfigIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    await set_setting(db, ALLOW_SIGNUPS, body.allow_signups)
    return {"ok": True, "allow_signups": body.allow_signups}


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(User).order_by(User.created_at))
    return list(rows)


async def _get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    return u


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
async def approve_user(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    u = await _get_user(db, user_id)
    u.status = "active"
    await db.commit()
    await db.refresh(u)
    return u


@router.post("/users/{user_id}/reject", response_model=AdminUserOut)
async def reject_user(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    u = await _get_user(db, user_id)
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode rejeitar a si mesmo")
    u.status = "rejected"
    await db.commit()
    await db.refresh(u)
    return u


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    u = await _get_user(db, user_id)
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode excluir a si mesmo")
    await db.delete(u)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Rede (allowlist de IP em runtime; host/porta/repo p/ o deploy) + atualização
# --------------------------------------------------------------------------- #
class NetworkIn(BaseModel):
    host: str = Field(default="0.0.0.0", max_length=64)
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_ips: list[str] = Field(default_factory=list, max_length=200)
    repo: str = Field(default="", max_length=200)  # "owner/repo" p/ checar updates
    branch: str = Field(default="main", max_length=100)


@router.get("/network")
async def get_network(admin: User = Depends(require_admin)):
    cfg = await network_config.load_config()
    s = get_settings()
    return {
        "host": cfg.get("host", "0.0.0.0"),
        "port": cfg.get("port", 8000),
        "allowed_ips": network_config.get_allowlist(),
        "repo": cfg.get("repo", ""),
        "branch": cfg.get("branch", "main"),
        "trust_proxy": s.trust_proxy,
        "web_origin": s.web_origin,
    }


@router.put("/network")
async def put_network(body: NetworkIn, admin: User = Depends(require_admin)):
    # limpa/normaliza IPs (a validação real é no ipaddress do network_config)
    cfg = body.model_dump()
    cfg["allowed_ips"] = [i.strip() for i in cfg["allowed_ips"] if i and i.strip()][:200]
    cfg["repo"] = cfg["repo"].strip()
    await network_config.save_config(cfg)
    return {"ok": True, **cfg, "allowed_ips": network_config.get_allowlist()}


@router.get("/update-check")
async def update_check(admin: User = Depends(require_admin)):
    """Compara a versão local com o GitHub (release mais recente + último commit)."""
    cfg = await network_config.load_config()
    repo = (cfg.get("repo") or "").strip()
    branch = (cfg.get("branch") or "main").strip()
    out: dict = {
        "current_version": __version__,
        "repo": repo,
        "branch": branch,
        "latest_release": None,
        "latest_commit": None,
        "update_available": False,
        "error": None,
    }
    if not repo:
        out["error"] = "Defina o repositório (owner/repo) para checar atualizações."
        return out
    try:
        async with httpx.AsyncClient(
            timeout=10, headers={"Accept": "application/vnd.github+json"}
        ) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code == 200:
                tag = (r.json() or {}).get("tag_name")
                out["latest_release"] = tag
                if tag:
                    out["update_available"] = tag.lstrip("v") != __version__.lstrip("v")
            c = await client.get(f"https://api.github.com/repos/{repo}/commits/{branch}")
            if c.status_code == 200:
                out["latest_commit"] = ((c.json() or {}).get("sha") or "")[:8]
            elif c.status_code == 404 and out["latest_release"] is None:
                out["error"] = "Repositório ou branch não encontrado (verifique owner/repo)."
    except httpx.HTTPError as exc:
        out["error"] = f"Falha ao consultar o GitHub: {exc}"
    return out
