"""Rotas de integrações externas (OAuth). Hoje: Google Workspace (Gmail + Agenda).

- Credenciais do app OAuth (client id/secret): globais, definidas pelo ADMIN na UI
  (não em env), guardadas em app_settings.
- Contas: cada usuário conecta VÁRIAS contas Google (tabela google_accounts). O
  consentimento é identificado pelo `state` assinado (não pelo cookie), então o
  callback funciona num redirect top-level.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_admin, require_approved
from .config import get_settings
from .db import get_db
from .integrations import github_service, google_service, ollama_service, tuya_service
from .models import GithubAccount, GoogleAccount, User
from .tools import sift_service

router = APIRouter(prefix="/integrations", tags=["integrations"])


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
