"""Integração Vercel (por-usuário, token cifrado).

Personal Access Token guardado em UserSecret (`VERCEL_TOKEN`). A conexão serve para
consultar projetos e deployments do usuário. As chamadas usam a API REST da Vercel
(https://api.vercel.com) com `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..secrets_service import VERCEL_TOKEN, get_secret, has_secret, set_secret

_BASE = "https://api.vercel.com"
_TIMEOUT = 15.0


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def get_token(db: AsyncSession, user_id: str) -> str | None:
    return await get_secret(db, uuid.UUID(user_id), VERCEL_TOKEN)


async def is_configured(db: AsyncSession, user_id: str) -> bool:
    return await has_secret(db, uuid.UUID(user_id), VERCEL_TOKEN)


async def set_token(db: AsyncSession, user_id: str, token: str) -> None:
    await set_secret(db, uuid.UUID(user_id), VERCEL_TOKEN, token.strip())


async def delete(db: AsyncSession, user_id: str) -> None:
    from sqlalchemy import delete as sa_delete

    from ..models import UserSecret
    await db.execute(
        sa_delete(UserSecret).where(
            UserSecret.user_id == uuid.UUID(user_id), UserSecret.name == VERCEL_TOKEN
        )
    )
    await db.commit()


async def test_connection(token: str) -> dict:
    """Valida o token: GET /v2/user. Retorna {ok, user?} ou {ok:False, error}."""
    if not token:
        return {"ok": False, "error": "token vazio"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/v2/user", headers=_headers(token))
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"falha de rede: {exc}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    u = (r.json() or {}).get("user") or {}
    return {"ok": True, "user": u.get("username") or u.get("email") or u.get("name") or "conectado"}


async def list_projects(token: str, limit: int = 20) -> list[dict]:
    """Projetos do usuário (nome, id, framework, último deploy)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{_BASE}/v9/projects", headers=_headers(token), params={"limit": max(1, min(limit, 100))})
    r.raise_for_status()
    out = []
    for p in (r.json() or {}).get("projects") or []:
        out.append({"id": p.get("id"), "name": p.get("name"), "framework": p.get("framework")})
    return out


async def list_deployments(token: str, project: str = "", limit: int = 20) -> list[dict]:
    """Deployments recentes (opcionalmente de um projeto), com estado e url."""
    params: dict = {"limit": max(1, min(limit, 100))}
    if project:
        params["projectId" if project.startswith("prj_") else "app"] = project
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{_BASE}/v6/deployments", headers=_headers(token), params=params)
    r.raise_for_status()
    out = []
    for d in (r.json() or {}).get("deployments") or []:
        out.append({
            "uid": d.get("uid"), "name": d.get("name"), "url": d.get("url"),
            "state": d.get("state") or d.get("readyState"), "created": d.get("created"),
        })
    return out
