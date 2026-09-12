"""Rotas de integrações externas (OAuth). Hoje: Google Workspace (Gmail + Agenda).

- Credenciais do app OAuth (client id/secret): globais, definidas pelo ADMIN na UI
  (não em env), guardadas em app_settings.
- Contas: cada usuário conecta VÁRIAS contas Google (tabela google_accounts). O
  consentimento é identificado pelo `state` assinado (não pelo cookie), então o
  callback funciona num redirect top-level.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import crypto
from .auth.deps import require_admin, require_approved
from .config import get_settings
from .db import get_db
from .integrations import (
    elevenlabs_service,
    github_service,
    google_service,
    notion_service,
    ollama_service,
    openrouter_oauth,
    providers_service,
    slack_service,
    spotify_service,
    tuya_service,
    vercel_service,
)
from .models import GithubAccount, GoogleAccount, NotionAccount, SlackAccount, User
from .secrets_service import OPENROUTER_KEY, set_secret
from .tools import sift_service

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _safe_http_origin(value: str | None) -> str:
    """Reduz uma URL/origin a `scheme://host[:port]` ou rejeita.

    O valor normalmente vem do header Origin de um POST autenticado. Remover
    path/query/fragment e recusar credenciais evita persistir um open redirect.
    """
    if not value:
        return ""
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    host = parsed.hostname
    if ":" in host:  # IPv6 precisa dos colchetes na autoridade reconstruída
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{f':{port}' if port is not None else ''}"


async def _accounts(db: AsyncSession, user_id: uuid.UUID) -> list[GoogleAccount]:
    return list(
        await db.scalars(
            select(GoogleAccount)
            .where(GoogleAccount.user_id == user_id)
            .order_by(GoogleAccount.created_at)
        )
    )


@router.get("/google")
async def google_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    cfg = await google_service.get_oauth_config(db)
    accounts = await _accounts(db, user.id)
    return {
        "configured": cfg is not None,
        "is_admin": user.role == "admin",
        "client_id": cfg["client_id"] if cfg else "",  # não-secreto; ajuda o admin a conferir
        "redirect_uri": get_settings().google_redirect_uri,
        "accounts": [
            {"id": str(a.id), "email": a.email, "connected_at": a.created_at.isoformat()}
            for a in accounts
        ],
    }


class OAuthConfigIn(BaseModel):
    client_id: str
    client_secret: str | None = None  # vazio = mantém o atual


@router.put("/google/oauth")
async def google_set_oauth(
    body: OAuthConfigIn,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not body.client_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client ID é obrigatório")
    # exige secret na primeira configuração (quando ainda não há um salvo)
    existing = await google_service.get_oauth_config(db)
    if existing is None and not (body.client_secret or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client Secret é obrigatório")
    await google_service.set_oauth_config(db, body.client_id, body.client_secret)
    return {"ok": True}


@router.get("/google/connect")
async def google_connect(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    cfg = await google_service.get_oauth_config(db)
    if cfg is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Google não configurado. Um administrador precisa definir o Client ID/Secret.",
        )
    return RedirectResponse(google_service.authorization_url(str(user.id), cfg))


@router.get("/google/callback")
async def google_callback(
    state: str = "", code: str = "", error: str = "", db: AsyncSession = Depends(get_db)
):
    web = get_settings().web_origin.rstrip("/")

    def _back(status_kv: str) -> RedirectResponse:
        return RedirectResponse(f"{web}/chat?{status_kv}")

    if error:
        return _back(f"google=error&reason={error}")
    user_id = google_service.verify_state(state)
    if not user_id or not code:
        return _back("google=error&reason=invalid_state")

    cfg = await google_service.get_oauth_config(db)
    if cfg is None:
        return _back("google=error&reason=not_configured")

    result = await google_service.exchange_code(code, cfg)
    if result.get("error"):
        return _back(f"google=error&reason={result['error'][:60]}")

    uid = uuid.UUID(user_id)
    user = await db.get(User, uid)
    if user is None:
        return _back("google=error&reason=user_not_found")

    email = result.get("email", "")
    # upsert por e-mail: reconectar a mesma conta atualiza o refresh token
    existing = None
    if email:
        existing = await db.scalar(
            select(GoogleAccount).where(
                GoogleAccount.user_id == uid, GoogleAccount.email == email
            )
        )
    if existing is not None:
        existing.refresh_token = result["refresh_token"]
        existing.scopes = result.get("scopes", "")
        google_service.forget(str(existing.id))
    else:
        db.add(
            GoogleAccount(
                user_id=uid,
                email=email,
                refresh_token=result["refresh_token"],
                scopes=result.get("scopes", ""),
            )
        )
    await db.commit()
    # as tools google passam a existir/funcionar → reconstrói a SIFT do usuário
    sift_service.invalidate(user_id)
    return _back("google=connected")


@router.post("/google/accounts/{account_id}/test")
async def google_test_account(
    account_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(GoogleAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    result = await google_service.test_account(str(acc.id))
    if result.get("ok"):
        return {"ok": True, "email": result.get("email") or acc.email}
    return {"ok": False, "error": "Não foi possível acessar esta conta — reconecte (o acesso pode ter expirado)."}


class GmailSendIn(BaseModel):
    account: str = ""   # id (ou e-mail) da conta Google a usar
    to: str
    subject: str = ""
    body: str = ""      # texto plano (fallback)
    cc: str = ""
    html: str = ""      # versão rica (opcional) — vinda do composer com formatação


@router.post("/google/gmail/send")
async def google_gmail_send(
    body: GmailSendIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Envia um e-mail revisado no composer do chat. Resolve a conta (deve ser do
    usuário), pega o token ao vivo e envia. Escopado ao usuário."""
    if not (body.to or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe ao menos um destinatário")
    accounts = await _accounts(db, user.id)
    if not accounts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhuma conta Google conectada")
    key = (body.account or "").strip().lower()
    acc = None
    if key:
        for a in accounts:
            if str(a.id).lower() == key or (a.email or "").lower() == key:
                acc = a
                break
    acc = acc or accounts[0]
    token = await google_service.get_access_token(str(acc.id))
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Não foi possível acessar esta conta — reconecte em Integrações.")
    try:
        res = await run_in_threadpool(
            google_service.gmail_send, token, body.to, body.subject, body.body, body.cc, body.html
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao enviar: {exc}")
    return {"ok": True, "id": res.get("id"), "from": acc.email}


@router.delete("/google/accounts/{account_id}")
async def google_disconnect_account(
    account_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(GoogleAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    if acc.refresh_token:
        await google_service.revoke(acc.refresh_token)
    google_service.forget(str(acc.id))
    await db.delete(acc)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# GitHub — tool. Conexão por PAT (colar token) OU OAuth App (client id/secret na
# UI). Cada usuário conecta VÁRIAS contas (github_accounts); o token é lido ao vivo.
# --------------------------------------------------------------------------- #
async def _gh_accounts(db: AsyncSession, user_id: uuid.UUID) -> list[GithubAccount]:
    return list(await db.scalars(
        select(GithubAccount).where(GithubAccount.user_id == user_id).order_by(GithubAccount.created_at)
    ))


@router.get("/github")
async def github_status(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await github_service.get_oauth_config(db)
    accounts = await _gh_accounts(db, user.id)
    return {
        "oauth_configured": cfg is not None,
        # login por código: só depende de um client_id público embutido no build
        "device_available": bool(github_service.device_client_id()),
        "is_admin": user.role == "admin",
        "client_id": cfg["client_id"] if cfg else "",
        "redirect_uri": get_settings().github_redirect_uri,
        "accounts": [
            {"id": str(a.id), "login": a.login, "auth_type": a.auth_type,
             "avatar_url": a.avatar_url, "connected_at": a.created_at.isoformat()}
            for a in accounts
        ],
    }


@router.put("/github/oauth")
async def github_set_oauth(
    body: OAuthConfigIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    if not body.client_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client ID é obrigatório")
    existing = await github_service.get_oauth_config(db)
    if existing is None and not (body.client_secret or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client Secret é obrigatório")
    await github_service.set_oauth_config(db, body.client_id, body.client_secret)
    return {"ok": True}


class GithubPatIn(BaseModel):
    token: str


@router.post("/github/pat")
async def github_connect_pat(
    body: GithubPatIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Conecta uma conta colando um Personal Access Token (fine-grained ou classic)."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o token")
    info = await github_service.validate_pat(token)
    if info.get("error") or not info.get("login"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Token inválido: {info.get('error', 'sem acesso')}")
    login = info["login"]
    existing = await db.scalar(
        select(GithubAccount).where(GithubAccount.user_id == user.id, GithubAccount.login == login)
    )
    if existing is not None:
        existing.token = token
        existing.auth_type = "pat"
        existing.refresh_token = ""
        existing.token_expires_at = None
        existing.scopes = info.get("scopes", "")
        existing.avatar_url = info.get("avatar_url", "")
        github_service.forget(str(existing.id))
    else:
        db.add(GithubAccount(
            user_id=user.id, login=login, token=token, auth_type="pat",
            scopes=info.get("scopes", ""), avatar_url=info.get("avatar_url", ""),
        ))
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True, "login": login}


class GithubDevicePollIn(BaseModel):
    handle: str


@router.post("/github/device/start")
async def github_device_start(user: User = Depends(require_approved)):
    """Inicia o Device Flow: devolve o código que o usuário digita no GitHub.

    O `device_code` NÃO volta em claro. Ele vai cifrado (junto do dono e de um
    prazo) num `handle` opaco: quem tivesse o device_code e o client_id — que é
    público — conseguiria reivindicar o token da autorização alheia."""
    client_id = github_service.device_client_id()
    if not client_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Login por código do GitHub indisponível nesta instalação "
            "(GITHUB_DEVICE_CLIENT_ID não configurado).",
        )
    r = await github_service.device_start(client_id)
    if r.get("error"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub recusou: {r['error']}")
    handle = crypto.encrypt(
        json.dumps({
            "device_code": r["device_code"],
            "user_id": str(user.id),
            "exp": int(time.time()) + r["expires_in"],
        })
    )
    return {
        "handle": handle,
        "user_code": r["user_code"],
        "verification_uri": r["verification_uri"],
        "interval": r["interval"],
        "expires_in": r["expires_in"],
    }


@router.post("/github/device/poll")
async def github_device_poll(
    body: GithubDevicePollIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Uma tentativa de conclusão. A UI repete respeitando o `interval`."""
    client_id = github_service.device_client_id()
    if not client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Device flow indisponível.")
    try:
        data = json.loads(crypto.decrypt(body.handle))
    except Exception:  # noqa: BLE001 - handle adulterado, de outra APP_SECRET, ou lixo
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sessão de login inválida.") from None
    if data.get("user_id") != str(user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sessão de login de outro usuário.")
    if int(data.get("exp") or 0) < time.time():
        return {"status": "error", "error": "expired"}

    r = await github_service.device_poll(client_id, data.get("device_code", ""))
    if r.get("status") != "ok":
        return r  # pending / slow_down / error — a UI decide se repete

    login = r.get("login", "")
    if not login:
        return {"status": "error", "error": "no_login"}
    expires = (
        datetime.now(timezone.utc) + timedelta(seconds=r["expires_in"])
        if r.get("expires_in") else None
    )
    existing = await db.scalar(
        select(GithubAccount).where(GithubAccount.user_id == user.id, GithubAccount.login == login)
    )
    if existing is not None:
        existing.token = r["access_token"]
        existing.refresh_token = r.get("refresh_token", "")
        existing.auth_type = "oauth"
        existing.token_expires_at = expires
        existing.scopes = r.get("scopes", "")
        existing.avatar_url = r.get("avatar_url", "")
        github_service.forget(str(existing.id))
    else:
        db.add(GithubAccount(
            user_id=user.id, login=login, token=r["access_token"],
            refresh_token=r.get("refresh_token", ""), auth_type="oauth",
            token_expires_at=expires, scopes=r.get("scopes", ""),
            avatar_url=r.get("avatar_url", ""),
        ))
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"status": "ok", "login": login}


@router.get("/github/connect")
async def github_connect(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await github_service.get_oauth_config(db)
    if cfg is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OAuth do GitHub não configurado. Um administrador precisa definir o Client ID/Secret — "
            "ou conecte colando um Personal Access Token.",
        )
    return RedirectResponse(github_service.authorization_url(str(user.id), cfg))


@router.get("/github/callback")
async def github_callback(
    state: str = "", code: str = "", error: str = "", db: AsyncSession = Depends(get_db),
):
    web = get_settings().web_origin.rstrip("/")

    def _back(kv: str) -> RedirectResponse:
        return RedirectResponse(f"{web}/chat?{kv}")

    if error:
        return _back(f"github=error&reason={error}")
    user_id = github_service.verify_state(state)
    if not user_id or not code:
        return _back("github=error&reason=invalid_state")
    cfg = await github_service.get_oauth_config(db)
    if cfg is None:
        return _back("github=error&reason=not_configured")
    result = await github_service.exchange_code(code, cfg)
    if result.get("error"):
        return _back(f"github=error&reason={result['error'][:60]}")
    uid = uuid.UUID(user_id)
    if await db.get(User, uid) is None:
        return _back("github=error&reason=user_not_found")
    login = result.get("login", "")
    from datetime import datetime, timedelta, timezone
    expires = (datetime.now(timezone.utc) + timedelta(seconds=result["expires_in"])
               if result.get("expires_in") else None)
    existing = await db.scalar(
        select(GithubAccount).where(GithubAccount.user_id == uid, GithubAccount.login == login)
    ) if login else None
    if existing is not None:
        existing.token = result["access_token"]
        existing.refresh_token = result.get("refresh_token", "")
        existing.auth_type = "oauth"
        existing.token_expires_at = expires
        existing.scopes = result.get("scopes", "")
        existing.avatar_url = result.get("avatar_url", "")
        github_service.forget(str(existing.id))
    else:
        db.add(GithubAccount(
            user_id=uid, login=login, token=result["access_token"],
            refresh_token=result.get("refresh_token", ""), auth_type="oauth",
            token_expires_at=expires, scopes=result.get("scopes", ""),
            avatar_url=result.get("avatar_url", ""),
        ))
    await db.commit()
    sift_service.invalidate(user_id)
    return _back("github=connected")


@router.post("/github/accounts/{account_id}/test")
async def github_test_account(
    account_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    acc = await db.get(GithubAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    result = await github_service.test_account(str(acc.id))
    if result.get("ok"):
        return {"ok": True, "login": result.get("login") or acc.login}
    return {"ok": False, "error": "Não foi possível acessar esta conta — reconecte (o token pode ter expirado ou sido revogado)."}


@router.delete("/github/accounts/{account_id}")
async def github_disconnect_account(
    account_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    acc = await db.get(GithubAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    github_service.forget(str(acc.id))
    await db.delete(acc)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Notion — tool. Conexão por token de integração interna (colar) OU OAuth (client
# id/secret na UI). Cada usuário conecta VÁRIAS contas (notion_accounts); o token é
# lido ao vivo (não expira). Espelha o GitHub.
# --------------------------------------------------------------------------- #
async def _notion_accounts(db: AsyncSession, user_id: uuid.UUID) -> list[NotionAccount]:
    return list(await db.scalars(
        select(NotionAccount).where(NotionAccount.user_id == user_id).order_by(NotionAccount.created_at)
    ))


@router.get("/notion")
async def notion_status(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await notion_service.get_oauth_config(db)
    accounts = await _notion_accounts(db, user.id)
    return {
        "oauth_configured": cfg is not None,
        "is_admin": user.role == "admin",
        "client_id": cfg["client_id"] if cfg else "",
        "redirect_uri": get_settings().notion_redirect_uri,
        "accounts": [
            {"id": str(a.id), "workspace": a.workspace, "auth_type": a.auth_type,
             "avatar_url": a.avatar_url, "connected_at": a.created_at.isoformat()}
            for a in accounts
        ],
    }


@router.put("/notion/oauth")
async def notion_set_oauth(
    body: OAuthConfigIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    if not body.client_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client ID é obrigatório")
    existing = await notion_service.get_oauth_config(db)
    if existing is None and not (body.client_secret or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client Secret é obrigatório")
    await notion_service.set_oauth_config(db, body.client_id, body.client_secret)
    return {"ok": True}


class NotionTokenIn(BaseModel):
    token: str


@router.post("/notion/token")
async def notion_connect_token(
    body: NotionTokenIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Conecta uma conta colando um token de integração interna (ntn_… / secret_…)."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o token")
    info = await notion_service.validate_token(token)
    if info.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Token inválido: {info.get('error', 'sem acesso')}")
    ws = info.get("workspace") or "Notion"
    bot_id = info.get("bot_id") or ""
    existing = await db.scalar(
        select(NotionAccount).where(NotionAccount.user_id == user.id, NotionAccount.bot_id == bot_id)
    ) if bot_id else None
    if existing is not None:
        existing.token = token
        existing.auth_type = "token"
        existing.workspace = ws
        existing.avatar_url = info.get("avatar_url", "")
    else:
        db.add(NotionAccount(
            user_id=user.id, workspace=ws, bot_id=bot_id, token=token, auth_type="token",
            avatar_url=info.get("avatar_url", ""),
        ))
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True, "workspace": ws}


@router.get("/notion/connect")
async def notion_connect(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await notion_service.get_oauth_config(db)
    if cfg is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OAuth do Notion não configurado. Um administrador precisa definir o Client ID/Secret — "
            "ou conecte colando um token de integração interna.",
        )
    return RedirectResponse(notion_service.authorization_url(str(user.id), cfg))


@router.get("/notion/callback")
async def notion_callback(
    state: str = "", code: str = "", error: str = "", db: AsyncSession = Depends(get_db),
):
    web = get_settings().web_origin.rstrip("/")

    def _back(kv: str) -> RedirectResponse:
        return RedirectResponse(f"{web}/chat?{kv}")

    if error:
        return _back(f"notion=error&reason={error}")
    user_id = notion_service.verify_state(state)
    if not user_id or not code:
        return _back("notion=error&reason=invalid_state")
    cfg = await notion_service.get_oauth_config(db)
    if cfg is None:
        return _back("notion=error&reason=not_configured")
    result = await notion_service.exchange_code(code, cfg)
    if result.get("error"):
        return _back(f"notion=error&reason={result['error'][:60]}")
    uid = uuid.UUID(user_id)
    if await db.get(User, uid) is None:
        return _back("notion=error&reason=user_not_found")
    ws = result.get("workspace") or "Notion"
    bot_id = result.get("bot_id") or ""
    existing = await db.scalar(
        select(NotionAccount).where(NotionAccount.user_id == uid, NotionAccount.bot_id == bot_id)
    ) if bot_id else None
    if existing is not None:
        existing.token = result["access_token"]
        existing.auth_type = "oauth"
        existing.workspace = ws
        existing.avatar_url = result.get("avatar_url", "")
    else:
        db.add(NotionAccount(
            user_id=uid, workspace=ws, bot_id=bot_id, token=result["access_token"],
            auth_type="oauth", avatar_url=result.get("avatar_url", ""),
        ))
    await db.commit()
    sift_service.invalidate(user_id)
    return _back("notion=connected")


@router.post("/notion/accounts/{account_id}/test")
async def notion_test_account(
    account_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    acc = await db.get(NotionAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    result = await notion_service.test_account(str(acc.id))
    if result.get("ok"):
        return {"ok": True, "workspace": result.get("workspace") or acc.workspace}
    return {"ok": False, "error": "Não foi possível acessar este workspace — reconecte (o token pode ter sido revogado)."}


@router.delete("/notion/accounts/{account_id}")
async def notion_disconnect_account(
    account_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    acc = await db.get(NotionAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    await db.delete(acc)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Slack — tool. Conexão por Bot User OAuth Token (colar) OU OAuth (client id/secret
# na UI). Cada usuário conecta VÁRIOS workspaces (slack_accounts). Espelha o Notion.
# --------------------------------------------------------------------------- #
async def _slack_accounts(db: AsyncSession, user_id: uuid.UUID) -> list[SlackAccount]:
    return list(await db.scalars(
        select(SlackAccount).where(SlackAccount.user_id == user_id).order_by(SlackAccount.created_at)
    ))


@router.get("/slack")
async def slack_status(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await slack_service.get_oauth_config(db)
    accounts = await _slack_accounts(db, user.id)
    return {
        "oauth_configured": cfg is not None,
        "is_admin": user.role == "admin",
        "client_id": cfg["client_id"] if cfg else "",
        "redirect_uri": get_settings().slack_redirect_uri,
        "accounts": [
            {"id": str(a.id), "team": a.team, "auth_type": a.auth_type,
             "avatar_url": a.avatar_url, "connected_at": a.created_at.isoformat()}
            for a in accounts
        ],
    }


@router.put("/slack/oauth")
async def slack_set_oauth(
    body: OAuthConfigIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    if not body.client_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client ID é obrigatório")
    existing = await slack_service.get_oauth_config(db)
    if existing is None and not (body.client_secret or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client Secret é obrigatório")
    await slack_service.set_oauth_config(db, body.client_id, body.client_secret)
    return {"ok": True}


class SlackTokenIn(BaseModel):
    token: str


@router.post("/slack/token")
async def slack_connect_token(
    body: SlackTokenIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Conecta um workspace colando um Bot User OAuth Token (xoxb-…) ou user token."""
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe o token")
    info = await slack_service.validate_token(token)
    if info.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Token inválido: {info.get('error', 'sem acesso')}")
    team = info.get("team") or "Slack"
    team_id = info.get("team_id") or ""
    existing = await db.scalar(
        select(SlackAccount).where(SlackAccount.user_id == user.id, SlackAccount.team_id == team_id)
    ) if team_id else None
    if existing is not None:
        existing.token = token
        existing.auth_type = "token"
        existing.team = team
        existing.bot_user_id = info.get("bot_user_id", "")
    else:
        db.add(SlackAccount(
            user_id=user.id, team=team, team_id=team_id, token=token, auth_type="token",
            bot_user_id=info.get("bot_user_id", ""),
        ))
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True, "team": team}


@router.get("/slack/connect")
async def slack_connect(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    cfg = await slack_service.get_oauth_config(db)
    if cfg is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OAuth do Slack não configurado. Um administrador precisa definir o Client ID/Secret — "
            "ou conecte colando um Bot User OAuth Token.",
        )
    return RedirectResponse(slack_service.authorization_url(str(user.id), cfg))


@router.get("/slack/callback")
async def slack_callback(
    state: str = "", code: str = "", error: str = "", db: AsyncSession = Depends(get_db),
):
    web = get_settings().web_origin.rstrip("/")

    def _back(kv: str) -> RedirectResponse:
        return RedirectResponse(f"{web}/chat?{kv}")

    if error:
        return _back(f"slack=error&reason={error}")
    user_id = slack_service.verify_state(state)
    if not user_id or not code:
        return _back("slack=error&reason=invalid_state")
    cfg = await slack_service.get_oauth_config(db)
    if cfg is None:
        return _back("slack=error&reason=not_configured")
    result = await slack_service.exchange_code(code, cfg)
    if result.get("error"):
        return _back(f"slack=error&reason={result['error'][:60]}")
    uid = uuid.UUID(user_id)
    if await db.get(User, uid) is None:
        return _back("slack=error&reason=user_not_found")
    team = result.get("team") or "Slack"
    team_id = result.get("team_id") or ""
    existing = await db.scalar(
        select(SlackAccount).where(SlackAccount.user_id == uid, SlackAccount.team_id == team_id)
    ) if team_id else None
    if existing is not None:
        existing.token = result["access_token"]
        existing.auth_type = "oauth"
        existing.team = team
        existing.bot_user_id = result.get("bot_user_id", "")
    else:
        db.add(SlackAccount(
            user_id=uid, team=team, team_id=team_id, token=result["access_token"],
            auth_type="oauth", bot_user_id=result.get("bot_user_id", ""),
        ))
    await db.commit()
    sift_service.invalidate(user_id)
    return _back("slack=connected")


@router.post("/slack/accounts/{account_id}/test")
async def slack_test_account(
    account_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    acc = await db.get(SlackAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    result = await slack_service.test_account(str(acc.id))
    if result.get("ok"):
        return {"ok": True, "team": result.get("team") or acc.team}
    return {"ok": False, "error": "Não foi possível acessar este workspace — reconecte (o token pode ter sido revogado)."}


@router.delete("/slack/accounts/{account_id}")
async def slack_disconnect_account(
    account_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    acc = await db.get(SlackAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta não encontrada")
    await db.delete(acc)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# ElevenLabs — conexão POR-USUÁRIO (API key). Serve como provedor de voz do sistema
# (o /voice/tts prefere a ElevenLabs quando ligada) E libera a tool de áudio
# (elevenlabs.audio.generate). Mesmo padrão da Higgsfield.
# --------------------------------------------------------------------------- #
@router.get("/elevenlabs")
async def elevenlabs_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    cfg = await elevenlabs_service.public_config(db, str(user.id))
    voices: list = []
    if cfg.get("connected"):
        conn = await elevenlabs_service.get_config(db, str(user.id))
        if conn:
            voices = await run_in_threadpool(elevenlabs_service.list_voices, conn["api_key"])
    return {**cfg, "voices": voices}


class ElevenLabsConfigIn(BaseModel):
    api_key: str | None = None  # vazio = mantém a atual
    model: str | None = None
    default_voice: str | None = None
    enabled: bool | None = None


@router.put("/elevenlabs")
async def elevenlabs_set_config(
    body: ElevenLabsConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    existing = await elevenlabs_service.is_configured(db, str(user.id))
    if not existing and not (body.api_key or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API key é obrigatória")
    await elevenlabs_service.set_config(
        db, str(user.id),
        api_key=body.api_key, model=body.model,
        default_voice=body.default_voice, enabled=body.enabled,
    )
    sift_service.invalidate(str(user.id))  # creds mudaram → rebuild da SIFT
    return await elevenlabs_status(user=user, db=db)


@router.delete("/elevenlabs")
async def elevenlabs_disconnect(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await elevenlabs_service.delete_config(db, str(user.id))
    sift_service.invalidate(str(user.id))
    return {"ok": True}


@router.post("/elevenlabs/test")
async def elevenlabs_test(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    conn = await elevenlabs_service.get_config(db, str(user.id))
    if conn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ElevenLabs não configurada.")
    result = await run_in_threadpool(elevenlabs_service.test_connection, conn["api_key"])
    if result.get("ok"):
        return {"ok": True}
    return {"ok": False, "error": result.get("error") or "Não foi possível conectar à ElevenLabs."}


# --------------------------------------------------------------------------- #
# Vercel — Personal Access Token por-usuário (projetos/deployments).
# --------------------------------------------------------------------------- #
@router.get("/vercel")
async def vercel_status(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    return {"connected": await vercel_service.is_configured(db, str(user.id))}


class VercelIn(BaseModel):
    token: str | None = None  # vazio = mantém o atual


@router.put("/vercel")
async def vercel_set(
    body: VercelIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    if not (body.token or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token é obrigatório")
    await vercel_service.set_token(db, str(user.id), body.token)
    return {"connected": True}


@router.delete("/vercel")
async def vercel_disconnect(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await vercel_service.delete(db, str(user.id))
    return {"ok": True}


@router.post("/vercel/test")
async def vercel_test(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    tok = await vercel_service.get_token(db, str(user.id))
    if not tok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Vercel não configurada.")
    return await vercel_service.test_connection(tok)


# --------------------------------------------------------------------------- #
# Spotify — Client Credentials por-usuário (busca no catálogo).
# --------------------------------------------------------------------------- #
@router.get("/spotify")
async def spotify_status(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    return {"connected": await spotify_service.is_configured(db, str(user.id))}


class SpotifyIn(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None  # vazio + já configurado = mantém


@router.put("/spotify")
async def spotify_set(
    body: SpotifyIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    cid = (body.client_id or "").strip()
    sec = (body.client_secret or "").strip()
    if not cid or not sec:
        # permite atualizar só um campo se já havia credenciais
        cur = await spotify_service.get_creds(db, str(user.id))
        if cur:
            cid = cid or cur[0]
            sec = sec or cur[1]
    if not cid or not sec:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Client ID e Client Secret são obrigatórios")
    await spotify_service.set_creds(db, str(user.id), cid, sec)
    return {"connected": True}


@router.delete("/spotify")
async def spotify_disconnect(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await spotify_service.delete(db, str(user.id))
    return {"ok": True}


@router.post("/spotify/test")
async def spotify_test(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    creds = await spotify_service.get_creds(db, str(user.id))
    if not creds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Spotify não configurado.")
    return await spotify_service.test_connection(creds[0], creds[1])


# --------------------------------------------------------------------------- #
# Ollama — modelos LOCAIS, config POR-USUÁRIO (só a base URL; sem chave).
# --------------------------------------------------------------------------- #
class OllamaIn(BaseModel):
    base_url: str | None = None
    enabled: bool | None = None


@router.get("/ollama")
async def ollama_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    cfg = await ollama_service.public_config(db, user.id)
    models: list = []
    if cfg["configured"] and cfg["enabled"]:
        models = await ollama_service.list_user_models(db, user.id)
    return {**cfg, "models": models}


@router.put("/ollama")
async def ollama_save(
    body: OllamaIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    await ollama_service.set_config(db, user.id, base_url=body.base_url, enabled=body.enabled)
    return await ollama_service.public_config(db, user.id)


@router.post("/ollama/test")
async def ollama_test(
    body: OllamaIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    base = (body.base_url or "").strip()
    if not base:
        cfg = await ollama_service.public_config(db, user.id)
        base = cfg["base_url"]
    return await ollama_service.test_connection(base)


@router.get("/ollama/models")
async def ollama_models(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Modelos locais do usuário, no formato dos seletores (best-effort → [])."""
    return await ollama_service.list_user_models(db, user.id)


# --------------------------------------------------------------------------- #
# Provedores customizados OpenAI-compatíveis (kie.ai, LiteLLM, …) — POR-USUÁRIO.
# Cada provedor tem nome + base URL + chave (cifrada); os modelos entram nos
# seletores prefixados com `@<slug>/<id>`. Só provedores com chave listam modelos.
# --------------------------------------------------------------------------- #
class ProviderIn(BaseModel):
    name: str | None = None
    base_url: str | None = None
    models: list[str] | None = None
    enabled: bool | None = None
    api_key: str | None = None


class ProviderTestIn(BaseModel):
    base_url: str
    api_key: str
    models: list[str] | None = None


@router.get("/providers")
async def providers_list(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Provedores do usuário (sem chave em claro) + presets p/ pré-preencher a UI."""
    return {
        "items": await providers_service.list_configs(db, user.id),
        "presets": providers_service.PRESETS,
    }


@router.put("/providers/{slug}")
async def providers_upsert(
    slug: str,
    body: ProviderIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    return await providers_service.upsert(
        db, user.id, slug=slug, name=body.name, base_url=body.base_url,
        models=body.models, enabled=body.enabled, api_key=body.api_key,
    )


@router.delete("/providers/{slug}")
async def providers_delete(
    slug: str,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    await providers_service.delete(db, user.id, slug)
    return {"ok": True}


@router.post("/providers/test")
async def providers_test(
    body: ProviderTestIn,
    user: User = Depends(require_approved),
):
    """Sonda a conexão sem tocar na config salva (via /models ou completion mínima)."""
    return await providers_service.test_connection(body.base_url, body.api_key, body.models)


@router.get("/providers/models")
async def providers_models(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Modelos de todos os provedores do usuário, no formato dos seletores (best-effort → [])."""
    return await providers_service.list_user_models(db, user.id)


# --------------------------------------------------------------------------- #
# OpenRouter — "Conectar" por OAuth PKCE (alternativa a colar a chave à mão).
# Sem client secret e sem cadastro de callback: funciona em qualquer instalação.
# --------------------------------------------------------------------------- #
@router.get("/providers/openrouter/connect")
async def openrouter_connect(user: User = Depends(require_approved)):
    return RedirectResponse(openrouter_oauth.authorization_url(str(user.id)))


@router.get("/providers/openrouter/callback/{state}")
async def openrouter_callback(
    state: str, code: str = "", error: str = "", db: AsyncSession = Depends(get_db)
):
    """Volta do OpenRouter: valida o state, troca o code e salva a chave do usuário.

    O `state` vem no CAMINHO (ver openrouter_oauth): a URL de autorização não tem
    parâmetro de state, então ele viaja dentro do próprio callback_url."""
    web = get_settings().web_origin.split(",")[0].strip().rstrip("/")

    def _back(kv: str) -> RedirectResponse:
        return RedirectResponse(f"{web}/chat?{kv}")

    if error:
        return _back(f"openrouter=error&reason={error[:60]}")
    parsed = openrouter_oauth.verify_state(state)
    if not parsed or not code:
        return _back("openrouter=error&reason=invalid_state")
    user_id, jti = parsed

    result = await openrouter_oauth.exchange_code(code, jti)
    if result.get("error"):
        return _back(f"openrouter=error&reason={result['error']}")

    uid = uuid.UUID(user_id)
    if await db.get(User, uid) is None:
        return _back("openrouter=error&reason=user_not_found")
    await set_secret(db, uid, OPENROUTER_KEY, result["key"])
    await db.commit()
    # a chave destrava os modelos do OpenRouter → a SIFT do usuário é remontada
    sift_service.invalidate(user_id)
    return _back("openrouter=connected")


# --------------------------------------------------------------------------- #
# Tuya / Smart Life — conexão POR-USUÁRIO (creds + catálogo de dispositivos).
# Cada usuário liga a própria casa (self-hosted compartilhado com família/amigos).
# --------------------------------------------------------------------------- #
@router.get("/tuya")
async def tuya_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    return await tuya_service.public_config(db, str(user.id))


class TuyaConfigIn(BaseModel):
    base_url: str = tuya_service.DEFAULT_BASE_URL
    access_id: str
    access_secret: str | None = None  # vazio = mantém o atual
    aliases: dict[str, Any] = {}  # apelidos em linguagem natural (avançado, opcional)


@router.put("/tuya")
async def tuya_set_config(
    body: TuyaConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Salva credenciais + apelidos. Os dispositivos/cenas vêm da sincronização."""
    if not body.access_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Access ID é obrigatório")
    existing = await tuya_service.get_config(db, str(user.id))
    if existing is None and not (body.access_secret or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Access Secret é obrigatório")
    await tuya_service.set_config(
        db,
        str(user.id),
        base_url=body.base_url,
        access_id=body.access_id,
        access_secret=body.access_secret,
        aliases=body.aliases,
    )
    sift_service.invalidate(str(user.id))  # apelidos/creds mudaram → rebuild da SIFT
    return {"ok": True}


@router.post("/tuya/sync")
async def tuya_sync(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Descobre dispositivos e cenas na conta Tuya e atualiza o catálogo."""
    conn = await tuya_service.get_config(db, str(user.id))
    if conn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tuya não configurado.")
    try:
        found = await run_in_threadpool(tuya_service.sync, conn)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Falha ao consultar a Tuya — verifique as credenciais.")
    if not found.get("devices"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Nenhum dispositivo encontrado. Vincule sua conta Smart Life ao projeto Cloud na Tuya.",
        )
    await tuya_service.set_config(
        db, str(user.id), devices=found["devices"], scenes=found["scenes"]
    )
    sift_service.invalidate(str(user.id))
    return {"devices": found["devices"], "scenes": found["scenes"]}


@router.post("/tuya/test")
async def tuya_test(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    conn = await tuya_service.get_config(db, str(user.id))
    if conn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tuya não configurado.")
    result = await run_in_threadpool(tuya_service.test_connection, conn)
    if result.get("ok"):
        return {"ok": True}
    return {"ok": False, "error": result.get("error") or "Não foi possível conectar ao Tuya."}


# --------------------------------------------------------------------------- #
# Assinaturas — usar ChatGPT Plus/Pro pelo login da conta, sem chave de API.
# Device code é o fluxo principal; o OAuth com callback local é o fallback.
# --------------------------------------------------------------------------- #
@router.get("/subscriptions/chatgpt")
async def chatgpt_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    from .integrations import chatgpt_service
    return await chatgpt_service.public_config(db, str(user.id))


@router.get("/subscriptions/chatgpt/models")
async def chatgpt_models(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Modelos codex/* p/ os seletores (mesmo shape do /integrations/ollama/models);
    lista vazia quando não conectado — os seletores nem mostram a seção."""
    from .integrations import chatgpt_service
    cfg = await chatgpt_service.public_config(db, str(user.id))
    if not cfg.get("connected"):
        return []
    # provider="OpenAI": é a etiqueta que o seletor mostra ao lado do nome (ModelPicker),
    # do mesmo jeito que "OpenRouter" — assim dá p/ diferenciar a origem de olho. O nome
    # fica limpo (sem sufixo "(ChatGPT)"), já que a etiqueta carrega essa informação.
    return [
        {"id": m, "name": m.removeprefix(chatgpt_service.MODEL_PREFIX), "provider": "OpenAI"}
        for m in cfg.get("models") or []
    ]


@router.post("/subscriptions/chatgpt/begin")
async def chatgpt_begin(
    request: Request,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Gera a URL PKCE e guarda a origem real da UI para o retorno automático."""
    from .integrations import chatgpt_service

    # `Origin` é a origem efetiva da UI que fez o POST (localhost, IP da LAN ou
    # domínio). Guardamos somente scheme+authority: o callback nunca aceita uma
    # URL de retorno livre, evitando transformar o endpoint em open redirect.
    return_origin = _safe_http_origin(request.headers.get("origin"))
    if not return_origin:
        return_origin = _safe_http_origin(request.headers.get("referer"))
    if not return_origin:
        return_origin = _safe_http_origin(get_settings().web_origin)
    url = await chatgpt_service.begin_auth(db, str(user.id), return_origin or "")
    return {"url": url}


@router.post("/subscriptions/chatgpt/device/begin")
async def chatgpt_device_begin(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Inicia o device flow oficial, que não depende de localhost/callback."""
    from .integrations import chatgpt_service

    out = await chatgpt_service.begin_device_auth(db, str(user.id))
    if out.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, out["error"])
    return out


@router.post("/subscriptions/chatgpt/device/poll")
async def chatgpt_device_poll(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Consulta uma vez o device flow; o frontend respeita o intervalo retornado."""
    from .integrations import chatgpt_service

    out = await chatgpt_service.poll_device_auth(db, str(user.id))
    if out.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, out["error"])
    if out.get("connected"):
        sift_service.invalidate(str(user.id))
    return out


class ChatgptFinishIn(BaseModel):
    pasted: str


@router.post("/subscriptions/chatgpt/finish")
async def chatgpt_finish(
    body: ChatgptFinishIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    from .integrations import chatgpt_service
    out = await chatgpt_service.finish_auth(db, str(user.id), body.pasted)
    if out.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, out["error"])
    sift_service.invalidate(str(user.id))
    return out


class ChatgptModelsIn(BaseModel):
    models: list[str]


@router.put("/subscriptions/chatgpt/models")
async def chatgpt_set_models(
    body: ChatgptModelsIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    from .integrations import chatgpt_service
    await chatgpt_service.set_models(db, str(user.id), body.models)
    return await chatgpt_service.public_config(db, str(user.id))


@router.delete("/subscriptions/chatgpt")
async def chatgpt_disconnect(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    from .integrations import chatgpt_service
    await chatgpt_service.disconnect(db, str(user.id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Higgsfield — conexão POR-USUÁRIO (API key + secret de cloud.higgsfield.ai).
# Libera a tool de geração de imagem/vídeo (higgsfield.media.generate).
# --------------------------------------------------------------------------- #
@router.get("/higgsfield")
async def higgsfield_status(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    from .integrations import higgsfield_service
    return await higgsfield_service.public_config(db, str(user.id))


class HiggsfieldConfigIn(BaseModel):
    api_key: str
    api_secret: str | None = None  # vazio = mantém o atual


@router.put("/higgsfield")
async def higgsfield_set_config(
    body: HiggsfieldConfigIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    from .integrations import higgsfield_service
    if not body.api_key.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API key é obrigatória")
    existing = await higgsfield_service.get_config(db, str(user.id))
    if existing is None and not (body.api_secret or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API secret é obrigatório")
    await higgsfield_service.set_config(db, str(user.id), body.api_key, body.api_secret or "")
    sift_service.invalidate(str(user.id))  # creds mudaram → rebuild da SIFT
    return {"ok": True}


@router.delete("/higgsfield")
async def higgsfield_disconnect(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    from .integrations import higgsfield_service
    await higgsfield_service.delete_config(db, str(user.id))
    sift_service.invalidate(str(user.id))
    return {"ok": True}


@router.post("/higgsfield/test")
async def higgsfield_test(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    from .integrations import higgsfield_service
    conn = await higgsfield_service.get_config(db, str(user.id))
    if conn is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Higgsfield não configurada.")
    result = await run_in_threadpool(higgsfield_service.test_connection, conn)
    if result.get("ok"):
        return {"ok": True}
    return {"ok": False, "error": result.get("error") or "Não foi possível conectar à Higgsfield."}


@router.get("/messaging/connections")
async def messaging_connections(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Conexoes de chat ATIVAS do usuario (WhatsApp/Telegram/Discord) achatadas em
    [{id, platform, label}] — usado pela engrenagem da tool de Mensagens p/ escolher
    por quais conexoes a IA pode agir."""
    from .integrations import messaging_service
    accounts = await messaging_service.gather_accounts(db, user.id)
    return {"accounts": accounts}
