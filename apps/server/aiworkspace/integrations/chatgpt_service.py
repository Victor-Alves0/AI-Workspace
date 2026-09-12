"""ChatGPT (assinatura Plus/Pro) → modelos Codex pelo login da conta.

O plano de assinatura da OpenAI cobre o backend do Codex; o login é o MESMO
OAuth do Codex CLI oficial. O fluxo principal usa device code: funciona mesmo
quando navegador e servidor estão em máquinas/URLs diferentes, e o painel
detecta a autorização por polling. O OAuth PKCE com callback fixo em
localhost:1455 fica como fallback quando device auth não estiver habilitado.
Sem chave de API: tokens da conta, renovados por refresh_token.

AVISO DE POLÍTICA (jul/2026): a OpenAI tolera esse uso por terceiros (não há
proibição como a da Anthropic, que baniu OAuth de assinatura em fev/2026), mas
também não o abençoou formalmente — o fluxo imita o Codex CLI e pode quebrar
se a OpenAI mudar a checagem. Uso pessoal, por conta e risco do usuário.

Storage por-usuário em `app_settings` sob `chatgpt:{user_id}` (tokens cifrados):
    {access_enc, refresh_enc, expires, account_id, email, plan}
Fluxo pendente (PKCE verifier+state) em `chatgpt_auth:{user_id}` — efêmero.

O adaptador de protocolo (Responses API ↔ chat/completions) mora em
`providers/chatgpt_codex.py`; aqui é só auth/tokens/instruções.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..app_config import get_setting, set_setting

logger = logging.getLogger(__name__)

# Constantes do OAuth do Codex CLI (openai/codex — públicas no binário oficial)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
DEVICE_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
DEVICE_VERIFY_URL = "https://auth.openai.com/codex/device"
SCOPE = "openid profile email offline_access"

# claim do JWT com os dados da conta ChatGPT
_JWT_CLAIM = "https://api.openai.com/auth"

# Modelos servidos pelo backend do Codex (ids SEM o prefixo do app). O usuário
# pode acrescentar variantes a que tiver direito no painel (ex.: um "-fast").
DEFAULT_MODELS = ["gpt-5", "gpt-5-codex"]
MODEL_PREFIX = "codex/"


def _key(user_id: str) -> str:
    return f"chatgpt:{user_id}"


def _auth_key(user_id: str) -> str:
    return f"chatgpt_auth:{user_id}"


def _encode_state(user_id: str) -> str:
    """State opaco e autenticado; permite ao callback identificar o usuário
    sem depender dos cookies da origem original (IP/domínio != localhost)."""
    payload = json.dumps(
        {"u": user_id, "n": secrets.token_hex(16), "iat": int(time.time())},
        separators=(",", ":"),
    )
    return crypto.encrypt(payload)


def _decode_state_user(state: str) -> str:
    try:
        payload = json.loads(crypto.decrypt(state))
        user_id = str(payload.get("u") or "")
        issued_at = int(payload.get("iat") or 0)
    except Exception:  # noqa: BLE001 - state externo/inválido deve só ser recusado
        return ""
    # O fluxo pendente já expira na prática quando o authorization code expira;
    # este teto também impede que um state antigo seja reaproveitado para lookup.
    if not user_id or issued_at < int(time.time()) - 15 * 60:
        return ""
    return user_id


# --------------------------------------------------------------------------- #
# OAuth PKCE — begin (URL) / finish (troca do code colado)
# --------------------------------------------------------------------------- #
def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def begin_auth(db: AsyncSession, user_id: str, return_origin: str = "") -> str:
    """Gera PKCE+state, guarda o fluxo pendente e devolve a URL de autorização."""
    verifier, challenge = _pkce_pair()
    state = _encode_state(user_id)
    await set_setting(db, _auth_key(user_id), {
        "verifier": verifier,
        "state": state,
        "created": int(time.time()),
        "return_origin": return_origin,
    })
    q = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # os dois abaixo são os que o Codex CLI envia — mantêm o fluxo idêntico
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    })
    return f"{AUTHORIZE_URL}?{q}"


def parse_pasted(value: str) -> dict[str, str]:
    """Aceita a URL de redirect inteira, `code#state` ou só o code."""
    v = (value or "").strip()
    if not v:
        return {}
    try:
        u = urlparse(v)
        if u.query:
            qs = parse_qs(u.query)
            return {k: qs[k][0] for k in ("code", "state") if k in qs}
    except ValueError:
        pass
    if "#" in v:
        code, state = v.split("#", 1)
        return {"code": code, "state": state}
    if "code=" in v:
        qs = parse_qs(v)
        return {k: qs[k][0] for k in ("code", "state") if k in qs}
    return {"code": v}


def _decode_jwt(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


async def _persist_token_response(
    db: AsyncSession,
    user_id: str,
    tok: dict[str, Any],
) -> dict[str, Any]:
    """Valida e persiste uma resposta do token endpoint."""
    if not tok.get("access_token") or not tok.get("refresh_token"):
        return {"error": "resposta de token incompleta da OpenAI"}
    claims = _decode_jwt(tok.get("id_token") or tok["access_token"])
    auth_claim = claims.get(_JWT_CLAIM) or {}
    row = {
        "access_enc": crypto.encrypt(tok["access_token"]),
        "refresh_enc": crypto.encrypt(tok["refresh_token"]),
        "expires": int(time.time()) + int(tok.get("expires_in") or 3600),
        "account_id": auth_claim.get("chatgpt_account_id") or "",
        "plan": auth_claim.get("chatgpt_plan_type") or "",
        "email": claims.get("email") or "",
        "models": DEFAULT_MODELS,
    }
    await set_setting(db, _key(user_id), row)
    await set_setting(db, _auth_key(user_id), None)
    _token_cache.pop(str(user_id), None)
    return await public_config(db, user_id)


async def _exchange_code(
    db: AsyncSession,
    user_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
        )
    if r.status_code >= 400:
        logger.warning("chatgpt oauth: troca falhou %s %s", r.status_code, r.text[:200])
        return {
            "error": (
                f"a OpenAI recusou o código (HTTP {r.status_code}) — "
                "ele expira rápido; recomece o login"
            )
        }
    return await _persist_token_response(db, user_id, r.json())


async def _finish_auth(
    db: AsyncSession,
    user_id: str,
    parsed: dict[str, str],
) -> dict[str, Any]:
    """Valida o fluxo pendente, troca o code e persiste os tokens."""
    pend = await get_setting(db, _auth_key(user_id))
    if not isinstance(pend, dict) or not pend.get("verifier"):
        return {"error": "nenhum login pendente — clique em Conectar de novo"}
    code = parsed.get("code")
    if not code:
        return {"error": "a OpenAI não devolveu o código de autorização"}
    if parsed.get("state") and parsed["state"] != pend.get("state"):
        return {"error": "state não confere — recomece o login (Conectar)"}
    return await _exchange_code(
        db, user_id, code, str(pend["verifier"]), REDIRECT_URI
    )


async def finish_auth(db: AsyncSession, user_id: str, pasted: str) -> dict[str, Any]:
    """Fallback manual: aceita URL inteira, `code#state` ou apenas o code."""
    parsed = parse_pasted(pasted)
    if not parsed.get("code"):
        return {"error": "não achei o `code` no que foi colado — cole a URL inteira da barra de endereço"}
    return await _finish_auth(db, user_id, parsed)


async def finish_auth_callback(
    db: AsyncSession,
    code: str,
    state: str,
) -> tuple[dict[str, Any], str, str]:
    """Conclui o callback sem cookie e devolve (status, usuário, origem).

    O usuário vem do state cifrado/assinado; a origem vem exclusivamente do
    fluxo autenticado salvo no begin, nunca de um parâmetro livre do callback.
    """
    user_id = _decode_state_user(state)
    if not user_id:
        return {"error": "login inválido ou expirado — tente conectar novamente"}, "", ""
    pend = await get_setting(db, _auth_key(user_id))
    return_origin = (
        str(pend.get("return_origin") or "") if isinstance(pend, dict) else ""
    )
    out = await _finish_auth(db, user_id, {"code": code, "state": state})
    return out, user_id, return_origin


# --------------------------------------------------------------------------- #
# Device code — fluxo principal, sem callback local e adequado a servidor remoto
# --------------------------------------------------------------------------- #
async def begin_device_auth(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Solicita o código de uso único exatamente como o Codex CLI oficial."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(DEVICE_CODE_URL, json={"client_id": CLIENT_ID})
    except httpx.HTTPError:
        logger.warning("chatgpt device auth: falha ao solicitar código", exc_info=True)
        return {"error": "não foi possível falar com a autenticação da OpenAI"}
    if r.status_code == 404:
        return {
            "error": (
                "login por código de dispositivo não está habilitado nesta conta; "
                "use o login alternativo abaixo"
            )
        }
    if r.status_code >= 400:
        logger.warning(
            "chatgpt device auth: solicitação falhou %s %s",
            r.status_code,
            r.text[:200],
        )
        return {"error": f"a OpenAI recusou o início do login (HTTP {r.status_code})"}
    try:
        body = r.json()
        device_auth_id = str(body["device_auth_id"])
        user_code = str(body.get("user_code") or body["usercode"])
        interval = max(1, min(15, int(str(body.get("interval") or "5").strip())))
    except (KeyError, TypeError, ValueError):
        return {"error": "resposta de login incompleta da OpenAI"}
    await set_setting(
        db,
        _auth_key(user_id),
        {
            "type": "device",
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "interval": interval,
            "created": int(time.time()),
        },
    )
    return {
        "url": DEVICE_VERIFY_URL,
        "user_code": user_code,
        "interval": interval,
    }


async def poll_device_auth(db: AsyncSession, user_id: str) -> dict[str, Any]:
    """Executa uma tentativa de polling; o navegador chama novamente no intervalo."""
    pend = await get_setting(db, _auth_key(user_id))
    if not isinstance(pend, dict) or pend.get("type") != "device":
        return {"error": "nenhum login por código pendente — clique em Conectar"}
    if int(pend.get("created") or 0) < int(time.time()) - 15 * 60:
        await set_setting(db, _auth_key(user_id), None)
        return {"error": "o código expirou — clique em Conectar para gerar outro"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": pend.get("device_auth_id"),
                    "user_code": pend.get("user_code"),
                },
            )
    except httpx.HTTPError:
        logger.warning("chatgpt device auth: polling falhou", exc_info=True)
        return {"pending": True, "retry_after": 5}
    if r.status_code in {403, 404}:
        return {"pending": True, "retry_after": int(pend.get("interval") or 5)}
    if r.status_code >= 400:
        logger.warning(
            "chatgpt device auth: polling recusado %s %s",
            r.status_code,
            r.text[:200],
        )
        return {"error": f"a OpenAI recusou a autorização (HTTP {r.status_code})"}
    try:
        body = r.json()
        code = str(body["authorization_code"])
        verifier = str(body["code_verifier"])
    except (KeyError, TypeError, ValueError):
        return {"error": "resposta de autorização incompleta da OpenAI"}
    return await _exchange_code(
        db, user_id, code, verifier, DEVICE_REDIRECT_URI
    )


async def disconnect(db: AsyncSession, user_id: str) -> None:
    await set_setting(db, _key(user_id), None)
    await set_setting(db, _auth_key(user_id), None)
    _token_cache.pop(str(user_id), None)


async def public_config(db: AsyncSession, user_id: str) -> dict[str, Any]:
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict) or not raw.get("access_enc"):
        return {"connected": False}
    return {
        "connected": True,
        "email": raw.get("email") or "",
        "plan": raw.get("plan") or "",
        "models": [f"{MODEL_PREFIX}{m}" for m in (raw.get("models") or DEFAULT_MODELS)],
    }


async def is_connected(db: AsyncSession, user_id: str) -> bool:
    raw = await get_setting(db, _key(user_id))
    return isinstance(raw, dict) and bool(raw.get("access_enc"))


async def set_models(db: AsyncSession, user_id: str, models: list[str]) -> None:
    """Lista de ids de modelo (sem prefixo) que o usuário quer expor nos seletores."""
    raw = await get_setting(db, _key(user_id))
    if not isinstance(raw, dict):
        return
    clean = [m.strip().removeprefix(MODEL_PREFIX) for m in models if m.strip()]
    raw["models"] = clean or DEFAULT_MODELS
    await set_setting(db, _key(user_id), raw)


# --------------------------------------------------------------------------- #
# Tokens p/ o adaptador (refresh automático) — chamado pelo provider, que só
# tem o user_id (viaja no lugar da api_key). Engine efêmero NullPool: o
# provider roda no event loop do servidor OU num loop próprio (threadpool de
# tools), e o pool global prende conexões a um loop só.
# --------------------------------------------------------------------------- #
_token_cache: dict[str, tuple[str, str, int]] = {}  # uid -> (access, account_id, expires)


async def _load_row(user_id: str) -> dict[str, Any] | None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            raw = await get_setting(db, _key(user_id))
            return raw if isinstance(raw, dict) else None
    finally:
        await eng.dispose()


async def _save_tokens(user_id: str, access: str, refresh: str, expires: int) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..config import get_settings
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            raw = await get_setting(db, _key(user_id))
            if isinstance(raw, dict):
                raw["access_enc"] = crypto.encrypt(access)
                raw["refresh_enc"] = crypto.encrypt(refresh)
                raw["expires"] = expires
                await set_setting(db, _key(user_id), raw)
    finally:
        await eng.dispose()


async def get_access(user_id: str) -> tuple[str, str]:
    """(access_token, account_id) válidos — renova se faltar <2min. Levanta
    RuntimeError com mensagem amigável quando não conectado/expirado."""
    uid = str(user_id)
    cached = _token_cache.get(uid)
    now = int(time.time())
    if cached and cached[2] - now > 120:
        return cached[0], cached[1]
    row = await _load_row(uid)
    if not row:
        raise RuntimeError("ChatGPT não conectado — conecte em Configurações → Conexões → Assinaturas.")
    try:
        access = crypto.decrypt(row["access_enc"])
        refresh = crypto.decrypt(row["refresh_enc"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("credenciais do ChatGPT indecifráveis (APP_SECRET mudou?) — reconecte.") from exc
    account_id = row.get("account_id") or ""
    if int(row.get("expires") or 0) - now > 120:
        _token_cache[uid] = (access, account_id, int(row["expires"]))
        return access, account_id
    # expirou → refresh
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": CLIENT_ID,
        })
    if r.status_code >= 400:
        raise RuntimeError("sessão do ChatGPT expirou — reconecte em Conexões → Assinaturas.")
    tok = r.json()
    access = tok.get("access_token") or ""
    refresh = tok.get("refresh_token") or refresh
    expires = now + int(tok.get("expires_in") or 3600)
    if not access:
        raise RuntimeError("refresh do ChatGPT sem access_token — reconecte.")
    await _save_tokens(uid, access, refresh, expires)
    _token_cache[uid] = (access, account_id, expires)
    return access, account_id


# --------------------------------------------------------------------------- #
# Instruções do Codex (o backend espera o prompt do Codex CLI) — cache em
# memória + disco (/tmp), TTL 24h; fallback embutido se o GitHub falhar.
# --------------------------------------------------------------------------- #
_INSTR_CACHE: dict[str, Any] = {"text": "", "at": 0.0}
_INSTR_TTL_S = 24 * 3600
_INSTR_FALLBACK = (
    "You are Codex, based on GPT-5. You are running as a coding agent in the "
    "Codex CLI on a user's computer. Answer the user's requests directly and "
    "concisely."
)


async def get_instructions() -> str:
    if _INSTR_CACHE["text"] and time.time() - _INSTR_CACHE["at"] < _INSTR_TTL_S:
        return _INSTR_CACHE["text"]
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            rel = await client.get("https://api.github.com/repos/openai/codex/releases/latest")
            tag = rel.json().get("tag_name") if rel.status_code == 200 else "main"
            r = await client.get(
                f"https://raw.githubusercontent.com/openai/codex/{tag}/codex-rs/core/gpt_5_codex_prompt.md"
            )
            if r.status_code == 200 and r.text.strip():
                _INSTR_CACHE.update(text=r.text, at=time.time())
                return r.text
    except Exception:  # noqa: BLE001
        logger.warning("codex instructions: fetch falhou; usando fallback", exc_info=True)
    if not _INSTR_CACHE["text"]:
        _INSTR_CACHE.update(text=_INSTR_FALLBACK, at=time.time())
    return _INSTR_CACHE["text"]
