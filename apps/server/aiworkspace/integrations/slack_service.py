"""Slack: conexão por-usuário (bot/user token OU OAuth) + acesso à Web API (TOOL).

Espelha o [[notion_service.py]]:
  - Credenciais do OAuth (client id/secret) são GLOBAIS, na UI, em `app_settings`
    (secret cifrado). Só p/ quem quiser o fluxo "Conectar" com botão.
  - Cada usuário conecta VÁRIOS workspaces (tabela `slack_accounts`): um Bot User
    OAuth Token colado (`auth_type="token"`) OU um token vindo do OAuth. O token é
    cifrado e NUNCA entra na instância SIFT — é lido ao vivo por conta.
  - As chamadas de API são SÍNCRONAS (httpx.Client); as tools rodam no threadpool.

A Web API do Slack SEMPRE devolve {ok: bool, error?: str}; `_call` normaliza isso.
Os tokens do Slack não expiram (a menos que a rotação esteja ligada, o que não
habilitamos), então `get_token` só devolve o token guardado.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..app_config import get_setting, set_setting
from ..config import get_settings

logger = logging.getLogger(__name__)

AUTH_URI = "https://slack.com/oauth/v2/authorize"
TOKEN_URI = "https://slack.com/api/oauth.v2.access"
API_BASE = "https://slack.com/api"

# escopos do bot pedidos no fluxo OAuth (leitura de canais/histórico + envio).
BOT_SCOPES = [
    "channels:read", "groups:read", "im:read", "mpim:read",
    "channels:history", "groups:history", "im:history", "mpim:history",
    "chat:write", "users:read",
]

OAUTH_SETTING_KEY = "slack_oauth"  # em app_settings: {client_id, client_secret_enc}
_STATE_TTL = 600


class SlackError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Credenciais do OAuth (globais, na UI) — app_settings
# --------------------------------------------------------------------------- #
async def get_oauth_config(db: AsyncSession) -> dict[str, str] | None:
    raw = await get_setting(db, OAUTH_SETTING_KEY)
    if not isinstance(raw, dict):
        return None
    client_id = (raw.get("client_id") or "").strip()
    enc = raw.get("client_secret_enc") or ""
    if not client_id or not enc:
        return None
    try:
        secret = crypto.decrypt(enc)
    except Exception:  # noqa: BLE001
        return None
    return {
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uri": get_settings().slack_redirect_uri,
    }


async def set_oauth_config(db: AsyncSession, client_id: str, client_secret: str | None) -> None:
    cur = await get_setting(db, OAUTH_SETTING_KEY)
    cur = cur if isinstance(cur, dict) else {}
    enc = cur.get("client_secret_enc") or ""
    if client_secret:
        enc = crypto.encrypt(client_secret.strip())
    await set_setting(db, OAUTH_SETTING_KEY, {"client_id": client_id.strip(), "client_secret_enc": enc})


async def is_configured(db: AsyncSession) -> bool:
    return await get_oauth_config(db) is not None


# --------------------------------------------------------------------------- #
# State assinado (CSRF) — sem tabela
# --------------------------------------------------------------------------- #
def sign_state(user_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "typ": "slack_oauth", "iat": now, "exp": now + _STATE_TTL},
        get_settings().app_secret,
        algorithm="HS256",
    )


def verify_state(state: str) -> str | None:
    try:
        data = jwt.decode(state, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "slack_oauth":
        return None
    return data.get("sub")


# --------------------------------------------------------------------------- #
# Fluxo OAuth
# --------------------------------------------------------------------------- #
def authorization_url(user_id: str, creds: dict[str, str]) -> str:
    params = {
        "client_id": creds["client_id"],
        "scope": ",".join(BOT_SCOPES),
        "redirect_uri": creds["redirect_uri"],
        "state": sign_state(user_id),
    }
    return str(httpx.URL(AUTH_URI, params=params))


async def exchange_code(code: str, creds: dict[str, str]) -> dict[str, Any]:
    """Troca o `code` por um bot token. Retorna {access_token, team, team_id,
    bot_user_id} ou {error}."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URI,
            data={
                "code": code,
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "redirect_uri": creds["redirect_uri"],
            },
        )
    try:
        tok = r.json()
    except Exception:  # noqa: BLE001
        return {"error": f"token exchange failed: {r.text[:200]}"}
    if not tok.get("ok"):
        return {"error": tok.get("error") or "token exchange failed"}
    team = tok.get("team") or {}
    return {
        "access_token": tok.get("access_token") or "",
        "team": team.get("name") or "",
        "team_id": team.get("id") or "",
        "bot_user_id": tok.get("bot_user_id") or "",
    }


async def validate_token(token: str) -> dict[str, Any]:
    """Valida um token via auth.test. Retorna {team, team_id, bot_user_id} ou {error}."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{API_BASE}/auth.test",
                                  headers={"Authorization": f"Bearer {token.strip()}"})
        j = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if not j.get("ok"):
        return {"error": j.get("error") or "token inválido"}
    return {
        "team": j.get("team") or "",
        "team_id": j.get("team_id") or "",
        "bot_user_id": j.get("user_id") or "",
    }


def _as_uuid(value: str):
    import uuid as _uuid

    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return value


async def get_token(account_id: str) -> str | None:
    """Token da conta (não expira → devolve direto). None = conta inexistente."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..models import SlackAccount

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            acc = await db.get(SlackAccount, _as_uuid(account_id))
            return acc.token if acc else None
    finally:
        await eng.dispose()


def forget(account_id: str) -> None:  # simetria com os outros services (sem cache aqui)
    return None


async def test_account(account_id: str) -> dict[str, Any]:
    token = await get_token(account_id)
    if not token:
        return {"ok": False}
    info = await validate_token(token)
    if info.get("error"):
        return {"ok": False}
    return {"ok": True, "team": info.get("team", "")}


# --------------------------------------------------------------------------- #
# Web API (SÍNCRONO — roda no threadpool de dispatch da SIFT)
# --------------------------------------------------------------------------- #
def _call(token: str, method: str, *, http: str = "GET", params: dict | None = None,
          data: dict | None = None) -> dict:
    """Chama um método da Web API do Slack. Levanta SlackError com o campo `error`
    da resposta (a API sempre responde 200 com {ok, error})."""
    url = f"{API_BASE}/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=30) as client:
            if http == "POST":
                headers["Content-Type"] = "application/json; charset=utf-8"
                r = client.post(url, headers=headers, json=data)
            else:
                r = client.get(url, headers=headers, params=params)
        j = r.json()
    except Exception as exc:  # noqa: BLE001
        raise SlackError(f"falha de rede: {exc}") from exc
    if not j.get("ok"):
        err = j.get("error") or f"HTTP {r.status_code}"
        raise SlackError(err)
    return j


def _user_names(token: str, ids: set[str]) -> dict[str, str]:
    """Resolve id→nome (best-effort; se faltar escopo users:read, devolve vazio)."""
    names: dict[str, str] = {}
    for uid in list(ids)[:40]:
        if not uid:
            continue
        try:
            j = _call(token, "users.info", params={"user": uid})
            u = j.get("user") or {}
            names[uid] = u.get("real_name") or u.get("name") or uid
        except SlackError:
            break  # sem escopo → para de tentar
    return names


def list_channels(token: str, limit: int = 50) -> list[dict]:
    j = _call(token, "conversations.list", params={
        "types": "public_channel,private_channel,im,mpim",
        "limit": min(max(1, limit), 200),
        "exclude_archived": "true",
    })
    out = []
    for c in (j.get("channels") or []):
        if not isinstance(c, dict):
            continue
        out.append({
            "id": c.get("id"),
            "name": c.get("name") or (("DM" if c.get("is_im") else "") or c.get("id")),
            "is_private": bool(c.get("is_private")),
            "is_im": bool(c.get("is_im")),
            "is_member": bool(c.get("is_member", True)),
        })
    return out


def channel_history(token: str, channel: str, limit: int = 20) -> list[dict]:
    j = _call(token, "conversations.history", params={
        "channel": channel.strip(), "limit": min(max(1, limit), 100),
    })
    msgs = [m for m in (j.get("messages") or []) if isinstance(m, dict)]
    names = _user_names(token, {m.get("user", "") for m in msgs})
    out = []
    for m in reversed(msgs):  # cronológico
        uid = m.get("user", "") or m.get("bot_id", "")
        out.append({
            "user": names.get(uid, uid),
            "text": m.get("text", ""),
            "ts": m.get("ts", ""),
        })
    return out


def post_message(token: str, channel: str, text: str) -> dict:
    j = _call(token, "chat.postMessage", http="POST",
              data={"channel": channel.strip(), "text": text})
    return {"ok": True, "channel": j.get("channel"), "ts": j.get("ts")}


def search_messages(token: str, query: str, limit: int = 20) -> list[dict]:
    """search.messages exige um USER token com escopo search:read (bot tokens não
    buscam) — a mensagem de erro do Slack já explica se faltar."""
    j = _call(token, "search.messages", params={
        "query": query, "count": min(max(1, limit), 100),
    })
    matches = ((j.get("messages") or {}).get("matches")) or []
    out = []
    for m in matches:
        if not isinstance(m, dict):
            continue
        out.append({
            "user": m.get("username", "") or (m.get("user", "")),
            "text": m.get("text", ""),
            "channel": (m.get("channel") or {}).get("name", ""),
            "permalink": m.get("permalink", ""),
            "ts": m.get("ts", ""),
        })
    return out
