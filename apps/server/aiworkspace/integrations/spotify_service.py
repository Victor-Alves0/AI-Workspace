"""Integração Spotify (por-usuário, credenciais cifradas).

Usa o fluxo **Client Credentials** (app do usuário: Client ID + Secret) — dá acesso
ao CATÁLOGO público (buscar faixas/artistas/álbuns, ler metadados), sem login do
usuário final. Controle de playback exige OAuth de usuário (fora deste escopo).

Credenciais em UserSecret (`SPOTIFY_CREDS`, JSON {id, secret}).
"""

from __future__ import annotations

import base64
import json
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..secrets_service import SPOTIFY_CREDS, get_secret, has_secret, set_secret

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API = "https://api.spotify.com/v1"
_TIMEOUT = 15.0


async def get_creds(db: AsyncSession, user_id: str) -> tuple[str, str] | None:
    raw = await get_secret(db, uuid.UUID(user_id), SPOTIFY_CREDS)
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    cid, sec = str(d.get("id") or ""), str(d.get("secret") or "")
    return (cid, sec) if cid and sec else None


async def is_configured(db: AsyncSession, user_id: str) -> bool:
    return await has_secret(db, uuid.UUID(user_id), SPOTIFY_CREDS)


async def set_creds(db: AsyncSession, user_id: str, client_id: str, client_secret: str) -> None:
    await set_secret(
        db, uuid.UUID(user_id), SPOTIFY_CREDS,
        json.dumps({"id": client_id.strip(), "secret": client_secret.strip()}),
    )


async def delete(db: AsyncSession, user_id: str) -> None:
    from sqlalchemy import delete as sa_delete

    from ..models import UserSecret
    await db.execute(
        sa_delete(UserSecret).where(
            UserSecret.user_id == uuid.UUID(user_id), UserSecret.name == SPOTIFY_CREDS
        )
    )
    await db.commit()


async def _app_token(client_id: str, client_secret: str) -> str:
    """Access token (Client Credentials). Lança em erro."""
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(
            _TOKEN_URL,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"token HTTP {r.status_code}: {r.text[:200]}")
    return (r.json() or {}).get("access_token") or ""


async def test_connection(client_id: str, client_secret: str) -> dict:
    if not (client_id and client_secret):
        return {"ok": False, "error": "credenciais vazias"}
    try:
        tok = await _app_token(client_id, client_secret)
    except (httpx.HTTPError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": bool(tok), "error": None if tok else "sem access_token"}


async def search(client_id: str, client_secret: str, query: str, kind: str = "track", limit: int = 10) -> list[dict]:
    """Busca no catálogo. `kind` = track|artist|album|playlist."""
    tok = await _app_token(client_id, client_secret)
    kind = kind if kind in ("track", "artist", "album", "playlist") else "track"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(
            f"{_API}/search",
            headers={"Authorization": f"Bearer {tok}"},
            params={"q": query, "type": kind, "limit": max(1, min(limit, 50))},
        )
    r.raise_for_status()
    items = ((r.json() or {}).get(f"{kind}s") or {}).get("items") or []
    out = []
    for it in items:
        artists = ", ".join(a.get("name", "") for a in (it.get("artists") or []))
        out.append({
            "name": it.get("name"),
            "artists": artists or None,
            "url": (it.get("external_urls") or {}).get("spotify"),
            "id": it.get("id"),
        })
    return out
