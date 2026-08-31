"""GitHub: conexão por-usuário (PAT ou OAuth) + acesso à REST API v3 (multi-conta).

Espelha o `google_service` na divisão de responsabilidades:
  - Credenciais do OAuth App (client id/secret) são GLOBAIS, na UI, em `app_settings`
    (secret cifrado). Só p/ quem quiser o fluxo "Conectar" com botão.
  - Cada usuário conecta VÁRIAS contas (tabela `github_accounts`): um Personal Access
    Token colado (`auth_type="pat"`) OU um access token OAuth. O token é cifrado e
    lido/renovado ao vivo por conta — NUNCA entra na instância SIFT.
  - As chamadas de API são SÍNCRONAS (httpx.Client); as tools rodam no threadpool de
    dispatch da SIFT, então blocam só um worker, não o event loop.

O `state` do OAuth é assinado com `app_secret` (pyjwt) carregando o user_id — sem
tabela de estado (como no Google).
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .. import crypto
from ..app_config import get_setting, set_setting
from ..config import get_settings

logger = logging.getLogger(__name__)

AUTH_URI = "https://github.com/login/oauth/authorize"
TOKEN_URI = "https://github.com/login/oauth/access_token"
API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"

# escopo pedido no fluxo OAuth: repo (privados) + leitura de usuário.
SCOPES = ["repo", "read:user"]

OAUTH_SETTING_KEY = "github_oauth"  # em app_settings: {client_id, client_secret_enc}
_STATE_TTL = 600

# account_id -> (access_token, expiry_epoch) — só p/ OAuth com expiração
_token_cache: dict[str, tuple[str, float]] = {}


class GithubError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Credenciais do OAuth App (globais, na UI) — app_settings
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
    except Exception:  # noqa: BLE001 - APP_SECRET trocado invalida o ciphertext
        return None
    return {
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uri": get_settings().github_redirect_uri,
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
        {"sub": user_id, "typ": "github_oauth", "iat": now, "exp": now + _STATE_TTL},
        get_settings().app_secret,
        algorithm="HS256",
    )


def verify_state(state: str) -> str | None:
    try:
        data = jwt.decode(state, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "github_oauth":
        return None
    return data.get("sub")


# --------------------------------------------------------------------------- #
# Fluxo OAuth
# --------------------------------------------------------------------------- #
def authorization_url(user_id: str, creds: dict[str, str]) -> str:
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": creds["redirect_uri"],
        "scope": " ".join(SCOPES),
        "state": sign_state(user_id),
    }
    return str(httpx.URL(AUTH_URI, params=params))


async def exchange_code(code: str, creds: dict[str, str]) -> dict[str, Any]:
    """Troca o `code` por token e resolve o login da conta.
    Retorna {access_token, refresh_token, expires_in, login, avatar_url, scopes} ou {error}."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URI,
            headers={"Accept": "application/json"},
            data={
                "code": code,
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "redirect_uri": creds["redirect_uri"],
            },
        )
        if r.status_code != 200:
            return {"error": f"token exchange failed: {r.text[:300]}"}
        tok = r.json()
        access = tok.get("access_token")
        if not access:
            return {"error": tok.get("error_description") or "no_access_token"}
        who = await client.get(
            f"{API_BASE}/user",
            headers={"Authorization": f"Bearer {access}", "X-GitHub-Api-Version": API_VERSION},
        )
        login = avatar = ""
        if who.status_code == 200:
            j = who.json()
            login, avatar = j.get("login", ""), j.get("avatar_url", "")
    return {
        "access_token": access,
        "refresh_token": tok.get("refresh_token") or "",
        "expires_in": int(tok.get("expires_in") or 0),
        "login": login,
        "avatar_url": avatar,
        "scopes": tok.get("scope", "") or " ".join(SCOPES),
    }


# --------------------------------------------------------------------------- #
# Device Flow — o caminho "botão", sem colar nada
# --------------------------------------------------------------------------- #
# Por que este fluxo e não o de sempre: o device flow NÃO usa client secret e NÃO
# usa redirect_uri. Sobra só o `client_id`, que é público — dá para vir embutido no
# build e valer para qualquer instalação (desktop, Docker, VPS), sem o admin ter que
# registrar um OAuth App e sem cadastrar o endereço de retorno de cada máquina. Em
# troca, o usuário digita um código curto no github.com/login/device.
DEVICE_CODE_URI = "https://github.com/login/device/code"


def device_client_id() -> str:
    """Client ID embutido do device flow ('' = indisponível nesta instalação)."""
    return (get_settings().github_device_client_id or "").strip()


async def device_start(client_id: str) -> dict[str, Any]:
    """Pede o par (device_code, user_code). {device_code, user_code,
    verification_uri, interval, expires_in} ou {error}."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                DEVICE_CODE_URI,
                headers={"Accept": "application/json"},
                data={"client_id": client_id, "scope": " ".join(SCOPES)},
            )
    except Exception as exc:  # noqa: BLE001 - rede
        return {"error": str(exc)}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    j = r.json()
    if j.get("error"):
        # o mais comum aqui é o device flow não estar habilitado no OAuth App
        return {"error": j.get("error_description") or j["error"]}
    return {
        "device_code": j.get("device_code", ""),
        "user_code": j.get("user_code", ""),
        "verification_uri": j.get("verification_uri") or "https://github.com/login/device",
        "interval": int(j.get("interval") or 5),
        "expires_in": int(j.get("expires_in") or 900),
    }


async def device_poll(client_id: str, device_code: str) -> dict[str, Any]:
    """Uma tentativa de troca do device_code por token.

    Retorna {status: "pending"} enquanto o usuário não autorizou, {status:
    "slow_down", interval} quando o GitHub pede mais espaço entre as tentativas,
    {status: "ok", ...dados da conta} no sucesso, {status: "error", error} no fim.
    Quem repete é o chamador — aqui não há laço, para não segurar um worker."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                TOKEN_URI,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
    except Exception as exc:  # noqa: BLE001 - rede
        return {"status": "error", "error": str(exc)}
    if r.status_code != 200:
        return {"status": "error", "error": f"HTTP {r.status_code}"}
    tok = r.json()
    err = tok.get("error")
    if err == "authorization_pending":
        return {"status": "pending"}
    if err == "slow_down":
        return {"status": "slow_down", "interval": int(tok.get("interval") or 10)}
    if err:
        # expired_token / access_denied / unsupported_grant_type — todos terminais
        return {"status": "error", "error": tok.get("error_description") or err}

    access = tok.get("access_token")
    if not access:
        return {"status": "error", "error": "no_access_token"}
    info = await validate_pat(access)
    return {
        "status": "ok",
        "access_token": access,
        "refresh_token": tok.get("refresh_token") or "",
        "expires_in": int(tok.get("expires_in") or 0),
        "login": info.get("login", ""),
        "avatar_url": info.get("avatar_url", ""),
        "scopes": tok.get("scope", "") or info.get("scopes", ""),
    }


async def validate_pat(token: str) -> dict[str, Any]:
    """Valida um Personal Access Token via GET /user. Retorna {login, avatar_url,
    scopes} ou {error}. Os scopes vêm do header X-OAuth-Scopes (fine-grained não
    envia esse header — fica vazio, e tudo bem)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {token.strip()}", "X-GitHub-Api-Version": API_VERSION},
            )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if r.status_code != 200:
        return {"error": f"token inválido (HTTP {r.status_code})"}
    j = r.json()
    return {
        "login": j.get("login", ""),
        "avatar_url": j.get("avatar_url", ""),
        "scopes": r.headers.get("X-OAuth-Scopes", ""),
    }


async def _refresh_oauth_token(refresh_token: str, creds: dict[str, str]) -> tuple[str, str, float]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URI,
            headers={"Accept": "application/json"},
            data={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    r.raise_for_status()
    tok = r.json()
    return (
        tok["access_token"],
        tok.get("refresh_token") or refresh_token,
        time.time() + int(tok.get("expires_in") or 3600),
    )


def _as_uuid(value: str):
    import uuid as _uuid

    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return value


async def get_token(account_id: str) -> str | None:
    """Token válido p/ uma conta conectada. PAT: devolve como está. OAuth com
    expiração: renova via refresh se venceu. None = conta inexistente ou refresh
    revogado."""
    from ..models import GithubAccount

    now = time.time()
    hit = _token_cache.get(account_id)
    if hit and hit[1] - 60 > now:
        return hit[0]

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            acc = await db.get(GithubAccount, _as_uuid(account_id))
            if acc is None:
                return None
            # PAT ou OAuth sem expiração declarada → token direto (não cacheia)
            if acc.auth_type != "oauth" or not acc.token_expires_at or not acc.refresh_token:
                return acc.token or None
            # OAuth expirável: renova se venceu
            exp = acc.token_expires_at.timestamp()
            if exp - 60 > now:
                _token_cache[account_id] = (acc.token, exp)
                return acc.token
            creds = await get_oauth_config(db)
            if not creds:
                return acc.token or None
            try:
                access, new_refresh, new_exp = await _refresh_oauth_token(acc.refresh_token, creds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Refresh do token GitHub falhou (conta %s): %s", account_id, exc)
                return None
            from datetime import datetime, timezone
            acc.token = access
            acc.refresh_token = new_refresh
            acc.token_expires_at = datetime.fromtimestamp(new_exp, tz=timezone.utc)
            await db.commit()
            _token_cache[account_id] = (access, new_exp)
            return access
    finally:
        await eng.dispose()


def forget(account_id: str) -> None:
    _token_cache.pop(account_id, None)


async def test_account(account_id: str) -> dict[str, Any]:
    forget(account_id)
    token = await get_token(account_id)
    if not token:
        return {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE}/user",
                headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": API_VERSION},
            )
        if r.status_code == 200:
            return {"ok": True, "login": r.json().get("login", "")}
    except Exception:  # noqa: BLE001
        pass
    return {"ok": False}


# --------------------------------------------------------------------------- #
# REST API v3 (SÍNCRONO — roda no threadpool de dispatch da SIFT)
# --------------------------------------------------------------------------- #
def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _request(token: str, method: str, path: str, *, params: dict | None = None,
             json: dict | None = None) -> Any:
    """Chamada REST síncrona. `path` começa com '/'. Levanta GithubError em erro,
    com a mensagem amigável da API (campo `message`)."""
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    try:
        with httpx.Client(timeout=30) as client:
            r = client.request(method, url, headers=_headers(token), params=params, json=json)
    except Exception as exc:  # noqa: BLE001
        raise GithubError(f"falha de rede: {exc}") from exc
    if r.status_code == 204:
        return {}
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {}
    if r.status_code >= 400:
        msg = (data.get("message") if isinstance(data, dict) else None) or f"HTTP {r.status_code}"
        errs = data.get("errors") if isinstance(data, dict) else None
        if errs:
            msg += f" ({errs})"
        raise GithubError(msg)
    return data


def list_repos(token: str, *, limit: int = 30, sort: str = "updated") -> list[dict]:
    rows = _request(token, "GET", "/user/repos",
                    params={"per_page": min(max(1, limit), 100), "sort": sort, "affiliation": "owner,collaborator,organization_member"})
    return [
        {"full_name": r.get("full_name"), "private": r.get("private"),
         "description": r.get("description") or "", "default_branch": r.get("default_branch"),
         "url": r.get("html_url"), "updated_at": r.get("updated_at")}
        for r in (rows or []) if isinstance(r, dict)
    ]


def get_file(token: str, repo: str, path: str, ref: str = "") -> dict:
    params = {"ref": ref} if ref else None
    data = _request(token, "GET", f"/repos/{repo}/contents/{path.lstrip('/')}", params=params)
    if isinstance(data, list):  # é um diretório
        return {"type": "dir", "path": path,
                "entries": [{"name": e.get("name"), "type": e.get("type"), "path": e.get("path")}
                            for e in data if isinstance(e, dict)]}
    content = data.get("content") or ""
    if data.get("encoding") == "base64" and content:
        try:
            text = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            text = "[conteúdo binário]"
    else:
        text = content
    return {"type": "file", "path": data.get("path", path), "sha": data.get("sha"),
            "size": data.get("size"), "content": text, "url": data.get("html_url")}


def search_code(token: str, query: str, repo: str = "", limit: int = 10) -> list[dict]:
    q = f"{query} repo:{repo}" if repo else query
    data = _request(token, "GET", "/search/code", params={"q": q, "per_page": min(max(1, limit), 30)})
    return [
        {"repo": (it.get("repository") or {}).get("full_name"), "path": it.get("path"),
         "url": it.get("html_url")}
        for it in (data.get("items") or []) if isinstance(it, dict)
    ]


def _issue_out(i: dict) -> dict:
    return {"number": i.get("number"), "title": i.get("title"), "state": i.get("state"),
            "user": (i.get("user") or {}).get("login"), "url": i.get("html_url"),
            "is_pr": "pull_request" in i, "body": i.get("body") or "",
            "comments": i.get("comments")}


def list_issues(token: str, repo: str, state: str = "open", limit: int = 20) -> list[dict]:
    rows = _request(token, "GET", f"/repos/{repo}/issues",
                    params={"state": state or "open", "per_page": min(max(1, limit), 100)})
    return [_issue_out(i) for i in (rows or []) if isinstance(i, dict)]


def get_issue(token: str, repo: str, number: int) -> dict:
    i = _request(token, "GET", f"/repos/{repo}/issues/{int(number)}")
    out = _issue_out(i)
    cmts = _request(token, "GET", f"/repos/{repo}/issues/{int(number)}/comments", params={"per_page": 30})
    out["comment_list"] = [{"user": (c.get("user") or {}).get("login"), "body": c.get("body") or ""}
                           for c in (cmts or []) if isinstance(c, dict)]
    return out


def create_issue(token: str, repo: str, title: str, body: str = "") -> dict:
    i = _request(token, "POST", f"/repos/{repo}/issues", json={"title": title, "body": body})
    return {"ok": True, "number": i.get("number"), "url": i.get("html_url")}


def comment_issue(token: str, repo: str, number: int, body: str) -> dict:
    c = _request(token, "POST", f"/repos/{repo}/issues/{int(number)}/comments", json={"body": body})
    return {"ok": True, "url": c.get("html_url")}


def list_prs(token: str, repo: str, state: str = "open", limit: int = 20) -> list[dict]:
    rows = _request(token, "GET", f"/repos/{repo}/pulls",
                    params={"state": state or "open", "per_page": min(max(1, limit), 100)})
    return [
        {"number": p.get("number"), "title": p.get("title"), "state": p.get("state"),
         "user": (p.get("user") or {}).get("login"), "head": (p.get("head") or {}).get("ref"),
         "base": (p.get("base") or {}).get("ref"), "url": p.get("html_url")}
        for p in (rows or []) if isinstance(p, dict)
    ]


def get_pr(token: str, repo: str, number: int) -> dict:
    p = _request(token, "GET", f"/repos/{repo}/pulls/{int(number)}")
    return {"number": p.get("number"), "title": p.get("title"), "state": p.get("state"),
            "user": (p.get("user") or {}).get("login"), "head": (p.get("head") or {}).get("ref"),
            "base": (p.get("base") or {}).get("ref"), "body": p.get("body") or "",
            "merged": p.get("merged"), "mergeable": p.get("mergeable"), "url": p.get("html_url")}


def create_pr(token: str, repo: str, title: str, head: str, base: str, body: str = "") -> dict:
    p = _request(token, "POST", f"/repos/{repo}/pulls",
                 json={"title": title, "head": head, "base": base, "body": body})
    return {"ok": True, "number": p.get("number"), "url": p.get("html_url")}


def put_file(token: str, repo: str, path: str, content: str, message: str,
             branch: str = "", sha: str = "") -> dict:
    body: dict[str, Any] = {
        "message": message or f"Update {path}",
        "content": base64.b64encode((content or "").encode("utf-8")).decode(),
    }
    if branch:
        body["branch"] = branch
    if sha:
        body["sha"] = sha
    data = _request(token, "PUT", f"/repos/{repo}/contents/{path.lstrip('/')}", json=body)
    commit = (data.get("commit") or {}) if isinstance(data, dict) else {}
    return {"ok": True, "commit": commit.get("sha"), "url": (data.get("content") or {}).get("html_url")}
