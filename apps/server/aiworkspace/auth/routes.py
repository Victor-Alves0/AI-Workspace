"""Rotas de autenticação: registro, login, refresh, logout, /me."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit_service, twofa_service
from ..app_config import signups_allowed
from ..config import get_settings
from ..db import get_db
from ..models import User
from ..schemas.auth import ChangePasswordIn, LoginIn, RegisterIn, UserOut
from .deps import ACCESS_COOKIE, REFRESH_COOKIE, current_user
from .ratelimit import check_login_rate
from .security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    dummy_verify,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Lock transacional global no banco. O valor é somente um namespace estável deste
# fluxo; advisory locks não precisam corresponder a uma linha/tabela.
_REGISTRATION_LOCK_ID = 0x41495752  # "AIWR"


def _chatgpt_return_url(origin: str, connected: bool, reason: str = "") -> str:
    """Destino do callback. `origin` já foi reduzida a scheme+authority no begin."""
    from urllib.parse import urlencode

    safe_origin = origin.rstrip("/") if origin else get_settings().web_origin.rstrip("/")
    query = {"chatgpt": "connected" if connected else "error"}
    if reason and not connected:
        query["reason"] = reason[:240]
    return f"{safe_origin}/chat?{urlencode(query)}"


@router.get("/callback")
async def chatgpt_oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    """Callback fixo do cliente público do Codex (`localhost:1455`).

    Não usa cookie: o state opaco identifica o fluxo e a origem autenticados no
    começo do login. Depois da troca, devolve o navegador à UI original.
    """
    from ..integrations import chatgpt_service
    from ..tools import sift_service

    if error:
        # Mesmo em cancelamento, tenta recuperar a origem pelo fluxo pendente.
        out, _user_id, origin = await chatgpt_service.finish_auth_callback(db, "", state)
        reason = error_description or error or out.get("error") or "autorização cancelada"
        return RedirectResponse(_chatgpt_return_url(origin, False, reason))

    out, user_id, origin = await chatgpt_service.finish_auth_callback(db, code, state)
    if out.get("error"):
        return RedirectResponse(_chatgpt_return_url(origin, False, out["error"]))
    if user_id:
        sift_service.invalidate(user_id)
    return RedirectResponse(_chatgpt_return_url(origin, True))


async def _lock_registration(db: AsyncSession) -> None:
    """Serializa cadastro para a decisão atômica de quem é o primeiro admin.

    `SELECT count(*)` sozinho permite que duas transações em uma instalação vazia
    leiam zero ao mesmo tempo e ambas criem um administrador. O lock é liberado no
    commit/rollback da própria sessão e também coordena múltiplos workers/processos.
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                     {"lock_id": _REGISTRATION_LOCK_ID})


@router.get("/config")
async def auth_config(db: AsyncSession = Depends(get_db)):
    """Config pública usada pela tela de login (ex.: mostrar ou não 'Cadastrar')."""
    return {"allow_signups": await signups_allowed(db)}


def _set_auth_cookies(response: Response, user: User) -> None:
    s = get_settings()
    secure = s.web_origin.startswith("https")
    uid = str(user.id)
    tv = user.token_version
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(uid, tv),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=s.access_token_ttl_min * 60,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(uid, tv),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=s.refresh_token_ttl_days * 86400,
        path="/",
    )


@router.post("/register", response_model=UserOut)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    check_login_rate(request, body.email)
    # Deve vir antes de toda leitura que decide se o cadastro é permitido e quem
    # vira admin. Quem esperou pelo lock observa o commit anterior na próxima SQL.
    await _lock_registration(db)
    if not await signups_allowed(db):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cadastro desabilitado pelo admin")

    exists = await db.scalar(select(User).where(User.email == body.email))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "E-mail já cadastrado")

    # primeiro usuário do sistema vira admin e já entra ativo; os demais ficam
    # pendentes de aprovação pelo admin.
    count = await db.scalar(select(func.count()).select_from(User))
    is_first = count == 0
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        role="admin" if is_first else "user",
        status="active" if is_first else "pending",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    _set_auth_cookies(response, user)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    check_login_rate(request, body.email)
    user = await db.scalar(select(User).where(User.email == body.email))
    # timing constante: se o usuário não existe, gasta ~o mesmo tempo de um verify
    # real antes de recusar — não dá para enumerar e-mails pelo tempo de resposta.
    if user is None:
        dummy_verify()
        await audit_service.record("login_failed", request=request, detail={"email": body.email, "reason": "no_user"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas")
    if not verify_password(body.password, user.hashed_password):
        await audit_service.record("login_failed", user_id=user.id, request=request, detail={"reason": "bad_password"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuário desativado")

    # 2º fator: se ligado, exige o código do app autenticador. Sem código, sinaliza
    # ao front (detail "2fa_required") p/ ele pedir; código errado = "2fa_invalid".
    if user.totp_enabled:
        if not (body.totp_code or "").strip():
            await audit_service.record("login_2fa_required", user_id=user.id, request=request)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2fa_required")
        if not twofa_service.verify(user.totp_secret, body.totp_code):
            await audit_service.record("login_failed", user_id=user.id, request=request, detail={"reason": "bad_2fa"})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2fa_invalid")

    _set_auth_cookies(response, user)
    await audit_service.record("login", user_id=user.id, request=request)
    return user


@router.post("/refresh", response_model=UserOut)
async def refresh(
    response: Response,
    aw_refresh: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not aw_refresh:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sem refresh token")
    import uuid

    try:
        user_id, token_version = decode_token(aw_refresh, "refresh")
        uid = uuid.UUID(user_id)
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh inválido")

    user = await db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inválido")
    # revogação: refresh emitido antes de um bump de token_version não vale mais
    if token_version != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão revogada; faça login novamente")

    _set_auth_cookies(response, user)  # rotação
    return user


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    aw_refresh: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
):
    # revoga a sessão do lado do servidor (bump em token_version) — assim um
    # refresh/access token vazado não continua válido após o logout.
    if aw_refresh:
        try:
            import uuid

            user_id, _ = decode_token(aw_refresh, "refresh")
            user = await db.get(User, uuid.UUID(user_id))
            if user is not None:
                user.token_version += 1
                await db.commit()
                await audit_service.record("logout", user_id=user.id, request=request)
        except (jwt.PyJWTError, ValueError):
            pass
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha atual incorreta")
    user.hashed_password = hash_password(body.new_password)
    # revoga todas as sessões antigas (invalida tokens emitidos com a senha antiga)
    user.token_version += 1
    await db.commit()
    await db.refresh(user)
    await audit_service.record("password_changed", user_id=user.id, request=request)
    # reemite cookies para a sessão atual não cair imediatamente após a troca
    _set_auth_cookies(response, user)
    return {"ok": True}
