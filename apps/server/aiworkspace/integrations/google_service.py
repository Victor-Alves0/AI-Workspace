"""Google Workspace: OAuth + acesso a Gmail e Agenda (multi-conta).

Como a conexão funciona ("Conectar agora", sem o usuário tocar no Google Cloud):
  - App OAuth: o EMBUTIDO do projeto (`builtin_app`) ou um PRÓPRIO que o admin salva
    na UI (sobrepõe o embutido). Cada conta guarda o `client_id` que emitiu o token,
    porque só aquele app consegue renová-lo.
  - Retorno: o Google devolve o navegador à PÁGINA DE RETORNO do projeto
    (`oauth_relay_url`, estática), que o reencaminha para `ret + cb` gravados no
    `state`. Assim um único endereço cadastrado no Google serve a qualquer
    instalação — IP de LAN, celular, desktop (localhost:41414) ou domínio.
  - PKCE (S256): o `code` passa pela página de retorno, então sozinho não pode
    valer nada. O verificador é derivado do `app_secret` + nonce da tentativa (não
    sai do servidor e não precisa de tabela).
  - Cada tentativa tem um nonce; a tela consulta `attempt(nonce)` até o callback
    registrar o resultado (a aba do Google abre FORA do app — no desktop, no
    navegador do sistema — e a página atual não navega).

Guardamos só o *refresh token* de cada conta (cifrado). O access token é buscado/
renovado ao vivo por conta e NUNCA entra na instância SIFT. Quando o Google recusa a
renovação, a conta é marcada (`broken_at`) para a tela pedir "Reconectar".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .. import crypto
from ..app_config import get_setting, set_setting
from ..config import get_settings

logger = logging.getLogger(__name__)

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
USERINFO_URI = "https://www.googleapis.com/oauth2/v2/userinfo"

# "Tudo" para Gmail + Agenda: ler/rotular/lixeira + enviar + agenda completa + e-mail.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
]

OAUTH_SETTING_KEY = "google_oauth"  # em app_settings: {client_id, client_secret_enc, redirect_uri?}
CALLBACK_PATH = "/integrations/google/callback"
_STATE_TTL = 900  # 15 min p/ concluir o consentimento

# App embutido do projeto. Tipo "Web" no Google Cloud, com a página de retorno
# (`oauth_relay_url`) cadastrada como redirect. `GOOGLE_APP_CLIENT_ID/SECRET` no
# ambiente sobrepõem estas constantes (ex.: build que injeta o segredo no CI).
_BUILTIN_CLIENT_ID = ""
_BUILTIN_CLIENT_SECRET = ""

# account_id -> (access_token, expiry_epoch)
_token_cache: dict[str, tuple[str, float]] = {}

BROKEN_MSG = "O Google recusou o acesso (revogado ou expirado). Reconecte a conta."


# --------------------------------------------------------------------------- #
# App OAuth: embutido ou próprio (admin, na UI)
# --------------------------------------------------------------------------- #
def relay_uri() -> str:
    return (get_settings().oauth_relay_url or "").strip()


def builtin_app() -> dict[str, str] | None:
    s = get_settings()
    cid = (s.google_app_client_id or _BUILTIN_CLIENT_ID).strip()
    sec = (s.google_app_client_secret or _BUILTIN_CLIENT_SECRET).strip()
    if not cid or not sec or not relay_uri():
        return None
    return {"client_id": cid, "client_secret": sec, "redirect_uri": relay_uri(), "source": "builtin"}


async def own_app(db: AsyncSession) -> dict[str, str] | None:
    """App próprio salvo pelo admin. None = não há (ou o APP_SECRET mudou)."""
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
    # app próprio volta DIRETO para esta instalação (GOOGLE_REDIRECT_URI), como o
    # OpenClaw/Hermes; a página de retorno do projeto é só do app embutido
    return {
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uri": (get_settings().google_redirect_uri or "").strip() or relay_uri(),
        "source": "own",
    }


async def get_oauth_config(db: AsyncSession) -> dict[str, str] | None:
    """App em vigor para NOVAS conexões: o próprio, senão o embutido. None = nenhum."""
    return await own_app(db) or builtin_app()


async def creds_for_client(db: AsyncSession, client_id: str) -> dict[str, str] | None:
    """Credenciais que renovam o token de uma conta: as do app que a emitiu. Conta
    antiga (sem `client_id`) ou app que sumiu → o app em vigor."""
    own, emb = await own_app(db), builtin_app()
    for c in (own, emb):
        if c and client_id and c["client_id"] == client_id:
            return c
    return own or emb


async def set_oauth_config(db: AsyncSession, client_id: str, client_secret: str | None) -> None:
    """Salva o app próprio (secret cifrado). `client_secret` vazio mantém o atual."""
    cur = await get_setting(db, OAUTH_SETTING_KEY)
    cur = cur if isinstance(cur, dict) else {}
    enc = cur.get("client_secret_enc") or ""
    if client_secret:
        enc = crypto.encrypt(client_secret.strip())
    await set_setting(db, OAUTH_SETTING_KEY, {"client_id": client_id.strip(), "client_secret_enc": enc})


async def clear_oauth_config(db: AsyncSession) -> None:
    """Remove o app próprio (volta ao embutido, se houver)."""
    await set_setting(db, OAUTH_SETTING_KEY, {})


async def is_configured(db: AsyncSession) -> bool:
    return await get_oauth_config(db) is not None


# --------------------------------------------------------------------------- #
# PKCE + state assinado (CSRF) — sem tabela
# --------------------------------------------------------------------------- #
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce_verifier(nonce: str) -> str:
    key = get_settings().app_secret.encode()
    return _b64(hmac.new(key, f"google-pkce:{nonce}".encode(), hashlib.sha256).digest())


def pkce_challenge(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def sign_state(user_id: str, nonce: str = "", ret: str = "") -> str:
    """`ret` (origem da API vista pelo navegador) e `cb` são lidos pela página de
    retorno — não são segredo; a assinatura impede a troca de usuário/tentativa."""
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "typ": "google_oauth", "n": nonce, "ret": ret, "cb": CALLBACK_PATH,
         "iat": now, "exp": now + _STATE_TTL},
        get_settings().app_secret,
        algorithm="HS256",
    )


def verify_state(state: str) -> dict[str, Any] | None:
    try:
        data = jwt.decode(state, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "google_oauth" or not data.get("sub"):
        return None
    return data


# --------------------------------------------------------------------------- #
# Tentativas de conexão (a tela consulta até o callback responder)
# --------------------------------------------------------------------------- #
# processo único (lock de instância), então memória basta
_attempts: dict[str, dict[str, Any]] = {}
_ATTEMPT_TTL = 1800


def new_attempt(user_id: str) -> str:
    now = time.time()
    for k in [k for k, v in _attempts.items() if now - v["ts"] > _ATTEMPT_TTL]:
        _attempts.pop(k, None)
    nonce = secrets.token_urlsafe(16)
    _attempts[nonce] = {"user": user_id, "status": "pending", "ts": now}
    return nonce


def attempt(nonce: str, user_id: str) -> dict[str, Any] | None:
    a = _attempts.get(nonce)
    return a if a and a["user"] == user_id else None


def finish_attempt(nonce: str, **fields: Any) -> None:
    if nonce in _attempts:
        _attempts[nonce].update(fields)


# --------------------------------------------------------------------------- #
# Fluxo OAuth (credenciais passadas explicitamente)
# --------------------------------------------------------------------------- #
def authorization_url(user_id: str, creds: dict[str, str], *, nonce: str = "", ret: str = "") -> str:
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": creds["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",       # queremos refresh token
        # consent: força o refresh token mesmo em re-consentimento;
        # select_account: sempre mostra o seletor (é "adicionar OUTRA conta")
        "prompt": "consent select_account",
        "include_granted_scopes": "true",
        "state": sign_state(user_id, nonce, ret),
    }
    if nonce:
        params["code_challenge"] = pkce_challenge(pkce_verifier(nonce))
        params["code_challenge_method"] = "S256"
    return str(httpx.URL(AUTH_URI, params=params))


async def exchange_code(code: str, creds: dict[str, str], nonce: str = "") -> dict[str, Any]:
    """Troca o `code` por tokens e resolve o e-mail da conta.

    Retorna {refresh_token, email, scopes} ou {error}."""
    data = {
        "code": code,
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "redirect_uri": creds["redirect_uri"],
        "grant_type": "authorization_code",
    }
    if nonce:
        data["code_verifier"] = pkce_verifier(nonce)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(TOKEN_URI, data=data)
        if r.status_code != 200:
            logger.warning("Troca do código Google falhou: %s", r.text[:300])
            return {"error": "exchange_failed"}
        tok = r.json()
        refresh = tok.get("refresh_token")
        access = tok.get("access_token")
        if not refresh:
            # sem refresh token não conseguimos agir depois (consentimento antigo
            # sem revogar): remova o acesso em myaccount.google.com e reconecte.
            return {"error": "no_refresh_token"}
        email = ""
        if access:
            ui = await client.get(USERINFO_URI, headers={"Authorization": f"Bearer {access}"})
            if ui.status_code == 200:
                email = ui.json().get("email", "")
    return {"refresh_token": refresh, "email": email, "scopes": tok.get("scope", "")}


class RefreshRejected(Exception):
    """O Google recusou o refresh token (revogado, expirado, app trocado)."""


async def _refresh_access_token(refresh_token: str, creds: dict[str, str]) -> tuple[str, float]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URI,
            data={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if r.status_code in (400, 401):
        # invalid_grant / unauthorized_client / invalid_client: não passa sozinho
        raise RefreshRejected(r.text[:200])
    r.raise_for_status()
    tok = r.json()
    return tok["access_token"], time.time() + int(tok.get("expires_in", 3600))


async def get_access_token(account_id: str) -> str | None:
    """Access token válido p/ uma conta conectada (renova se preciso).

    None = conta inexistente, app não configurado, ou refresh recusado — nesse caso
    a conta fica marcada (`broken_at`) para a tela pedir "Reconectar".
    """
    from ..models import GoogleAccount

    now = time.time()
    hit = _token_cache.get(account_id)
    if hit and hit[1] - 60 > now:  # margem de 60s
        return hit[0]
    # engine efêmero (NullPool): as tools rodam em threadpool via asyncio.run e o
    # pool async do SessionLocal é loop-bound (quebraria entre loops). Mesmo padrão
    # do automation/creator.
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            acc = await db.get(GoogleAccount, _as_uuid(account_id))
            if acc is None or not acc.refresh_token:
                return None
            creds = await creds_for_client(db, acc.client_id or "")
            if not creds:
                return None
            try:
                token, exp = await _refresh_access_token(acc.refresh_token, creds)
            except RefreshRejected as exc:
                logger.warning("Google recusou renovar o token (conta %s): %s", account_id, exc)
                acc.broken_at = datetime.now(timezone.utc)
                acc.last_error = BROKEN_MSG
                await db.commit()
                return None
            except Exception as exc:  # noqa: BLE001 - rede/5xx: passageiro, não marca
                logger.warning("Refresh do token Google falhou (conta %s): %s", account_id, exc)
                return None
            if acc.broken_at is not None or acc.last_error:
                acc.broken_at = None
                acc.last_error = ""
                await db.commit()
    finally:
        await eng.dispose()
    _token_cache[account_id] = (token, exp)
    return token


def _as_uuid(value: str):
    import uuid as _uuid

    try:
        return _uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return value


def forget(account_id: str) -> None:
    _token_cache.pop(account_id, None)


async def test_account(account_id: str) -> dict[str, Any]:
    """Verifica se a conta ainda funciona: fura o cache, força um refresh REAL no
    Google e confirma com um /userinfo. Retorna {ok, email} ou {ok: False}."""
    forget(account_id)  # ignora o token em cache — queremos um teste de verdade
    token = await get_access_token(account_id)
    if not token:
        return {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(USERINFO_URI, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return {"ok": True, "email": r.json().get("email", "")}
    except Exception:  # noqa: BLE001
        pass
    return {"ok": False}


async def revoke(refresh_token: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(REVOKE_URI, data={"token": refresh_token})
    except Exception as exc:  # noqa: BLE001 - best-effort
        logger.info("Revogação do token Google falhou (segue removendo local): %s", exc)


# --------------------------------------------------------------------------- #
# Clientes Gmail / Calendar (síncronos)
# --------------------------------------------------------------------------- #
# REST direto por httpx: o google-api-python-client pesava ~110 MB (as descrições de
# TODAS as APIs do Google) para as ~10 chamadas de Gmail e Agenda que fazemos.
_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
_CALENDAR = "https://www.googleapis.com/calendar/v3/calendars"


class _Google:
    """Cliente REST síncrono (as tools rodam em threadpool) com o token do usuário."""

    def __init__(self, token: str) -> None:
        self._c = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}"})

    def __enter__(self) -> "_Google":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._c.close()

    def call(self, method: str, url: str, *, params: dict[str, Any] | None = None,
             body: dict[str, Any] | None = None) -> dict[str, Any]:
        # o Google quer true/false minúsculos; vazio/None = parâmetro ausente
        q = {
            k: ("true" if v is True else "false" if v is False else v)
            for k, v in (params or {}).items() if v is not None and v != ""
        }
        r = self._c.request(method, url, params=q, json=body)
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except Exception:  # noqa: BLE001 - corpo sem o formato de erro do Google
                msg = r.text[:200]
            raise RuntimeError(f"Google {r.status_code}: {msg}")
        return r.json() if r.content else {}


def _cal_url(calendar_id: str, *resto: str) -> str:
    """URL de uma agenda: o id pode ser um e-mail (`x@group.calendar.google.com`)."""
    partes = [quote(calendar_id or "primary", safe="")] + [quote(r, safe="") for r in resto]
    return f"{_CALENDAR}/" + "/".join(partes)


def _hval(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


# ------------------------------- Gmail ------------------------------------- #
# Tetos da busca do Gmail. LISTAR ids é barato (1 chamada por até 500), mas cada
# mensagem custa 1 GET EXTRA de metadados (padrão N+1) — foi isso que fazia
# "quantos e-mails não lidos?" (max_results=0 → 500 GETs sequenciais) levar 3+
# minutos e estourar o turno. Agora: contagem = só ids (exata até o hard max);
# metadados = só uma amostra/teto.
GMAIL_SEARCH_HARD_MAX = 500   # teto de IDs paginados (contagem exata até aqui)
GMAIL_METADATA_MAX = 50       # teto de GETs N+1 quando o modelo pede N explícito
GMAIL_COUNT_SAMPLE = 25       # amostra com metadados quando max_results=0 (contar)


# Lixo que infla o contexto sem informar: e-mails de marketing enchem snippets e
# corpos de caracteres INVISÍVEIS (o LinkedIn manda centenas de U+034F seguidos p/
# empurrar o preview) e de réguas/espaçamento. Cada char desses é pago como token na
# entrada do modelo — e relido a cada volta do turno.
# Codepoints INVISIVEIS (largura zero / joiners / marcas de direcao). Mantidos como
# lista explicita: no fonte eles seriam indistinguiveis de espaco e corromperiam o
# arquivo em qualquer editor descuidado.
_INVISIBLE_CODEPOINTS = (
    0x00AD, 0x034F, 0x061C, 0x180E, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
    0x2028, 0x2029, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2060, 0x2061,
    0x2062, 0x2063, 0x2064, 0x2066, 0x2067, 0x2068, 0x2069, 0x206A, 0x206B,
    0x206C, 0x206D, 0x206E, 0x206F, 0x2800, 0xFEFF,
)
_INVISIBLE_RE = re.compile("[" + "".join(chr(c) for c in _INVISIBLE_CODEPOINTS) + "]")
_RULE_LINE_RE = re.compile(r"(?m)^[\s\-=_*~·•—–]{4,}$")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def _clean_text(s: str) -> str:
    """Tira invisíveis, réguas e espaçamento repetido — sem tocar no conteúdo real."""
    s = _INVISIBLE_RE.sub("", s or "")
    s = _RULE_LINE_RE.sub("", s)
    s = _MULTI_SPACE_RE.sub(" ", s)
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()


def gmail_search(token: str, query: str, max_results: int) -> dict[str, Any]:
    with _Google(token) as g:
        return _gmail_search(g, query, max_results)


def _gmail_search(g: _Google, query: str, max_results: int) -> dict[str, Any]:
    """Lista mensagens do Gmail (metadados + snippet).

    ``max_results``: quantas puxar (metadados; teto ``GMAIL_METADATA_MAX``).
    ``0`` (ou negativo) = CONTAR todas que casam a ``query`` (ids paginados até
    ``GMAIL_SEARCH_HARD_MAX``) e devolver metadados só das primeiras
    ``GMAIL_COUNT_SAMPLE`` — `count` é a contagem real."""
    count_all = max_results <= 0
    want_ids = GMAIL_SEARCH_HARD_MAX if count_all else min(int(max_results), GMAIL_METADATA_MAX)
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < want_ids:
        res = g.call("GET", f"{_GMAIL}/messages", params={
            "q": query or "", "maxResults": min(500, want_ids - len(ids)), "pageToken": page_token,
        })
        ids.extend(m["id"] for m in res.get("messages", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    meta_n = min(len(ids), GMAIL_COUNT_SAMPLE if count_all else want_ids)
    out = []
    for mid in ids[:meta_n]:
        full = g.call("GET", f"{_GMAIL}/messages/{quote(mid, safe='')}", params={
            "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"],
        })
        hs = full.get("payload", {}).get("headers", [])
        out.append({
            "id": full["id"],
            "from": _hval(hs, "From"),
            "subject": _hval(hs, "Subject"),
            "date": _hval(hs, "Date"),
            "snippet": _clean_text(full.get("snippet", "")),
        })
    result: dict[str, Any] = {"messages": out, "count": len(ids)}
    if len(ids) > len(out):
        result["note"] = f"{len(ids)} matching messages (metadata shown for the first {len(out)})"
    if count_all and page_token:
        result["note"] = f"{len(ids)}+ matching messages (hit the safety cap)"
    return result


def _decode_body(payload: dict) -> str:
    """Extrai text/plain (fallback text/html) de um payload Gmail (recursivo)."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data")
    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    html = ""
    for part in payload.get("parts", []) or []:
        txt = _decode_body(part)
        if txt and part.get("mimeType") == "text/plain":
            return txt
        if txt and not html:
            html = txt
    if not html and mime == "text/html" and data:
        html = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    return html


GMAIL_BODY_MAX = 4000  # teto por e-mail; ler 5 de uma vez já são 20k chars de contexto


def gmail_get(token: str, msg_id: str, max_chars: int = GMAIL_BODY_MAX) -> dict[str, Any]:
    with _Google(token) as g:
        full = g.call("GET", f"{_GMAIL}/messages/{quote(msg_id, safe='')}", params={"format": "full"})
    payload = full.get("payload", {})
    hs = payload.get("headers", [])
    body = _decode_body(payload)
    # remove HTML se caiu no fallback text/html
    if "<" in body and ">" in body:
        from .. import deep_search
        body = deep_search._html_to_text(body)
    body = _clean_text(body)
    out = {
        "id": full["id"],
        "from": _hval(hs, "From"),
        "subject": _hval(hs, "Subject"),
        "date": _hval(hs, "Date"),
        "body": body[:max_chars],
    }
    # o modelo precisa SABER que cortamos — senão ele afirma coisas sobre um e-mail
    # que leu pela metade sem nunca dizer que faltou pedaço.
    if len(body) > max_chars:
        out["truncated"] = True
        out["note"] = f"body cut at {max_chars} chars (original: {len(body)})"
    return out


def gmail_send(token: str, to: str, subject: str, body: str, cc: str = "", html: str = "") -> dict[str, Any]:
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    # `body` é o texto plano (fallback); `html`, quando presente, é a versão rica
    # (multipart/alternative) — o cliente escolhe a melhor que suporta.
    msg.set_content(body or (html and "Veja este e-mail em um cliente compatível com HTML.") or "")
    if html:
        msg.add_alternative(html, subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    with _Google(token) as g:
        sent = g.call("POST", f"{_GMAIL}/messages/send", body={"raw": raw})
    return {"ok": True, "id": sent.get("id")}


_MODIFY = {
    "archive": {"removeLabelIds": ["INBOX"]},
    "read": {"removeLabelIds": ["UNREAD"]},
    "unread": {"addLabelIds": ["UNREAD"]},
    "star": {"addLabelIds": ["STARRED"]},
    "unstar": {"removeLabelIds": ["STARRED"]},
}


def gmail_modify(token: str, msg_id: str, action: str) -> dict[str, Any]:
    url = f"{_GMAIL}/messages/{quote(msg_id, safe='')}"
    if action == "trash":
        with _Google(token) as g:
            g.call("POST", f"{url}/trash")
        return {"ok": True, "id": msg_id, "action": "trash"}
    mod = _MODIFY.get(action)
    if mod is None:
        return {"error": f"unknown action '{action}' (use archive/trash/read/unread/star/unstar)"}
    with _Google(token) as g:
        g.call("POST", f"{url}/modify", body=mod)
    return {"ok": True, "id": msg_id, "action": action}


# ------------------------------- Calendar ---------------------------------- #
def _when(value: str, tz: str = "") -> dict[str, str]:
    """Monta o campo start/end: com 'T' = dateTime; só data = evento de dia inteiro.
    Com `tz` (IANA) num dateTime SEM offset explícito, envia `timeZone` para o Google
    interpretar a hora no fuso do usuário (senão cairia no fuso padrão da agenda)."""
    if "T" not in (value or ""):
        return {"date": value}
    field = {"dateTime": value}
    has_offset = value.endswith("Z") or ("+" in value[10:]) or ("-" in value[10:])
    if tz and not has_offset:
        field["timeZone"] = tz
    return field


def _event_out(ev: dict) -> dict[str, Any]:
    out = {
        "id": ev.get("id"),
        "title": ev.get("summary", ""),
        "start": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"),
        "end": (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date"),
    }
    if ev.get("location"):  # só quando existe — resultado enxuto
        out["location"] = ev["location"]
    return out


_NAIVE_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rfc3339(s: str) -> str:
    """Sanitiza timeMin/timeMax vindos do MODELO: o Google exige RFC3339 com
    offset e responde 400 a datetimes 'naive' (ex.: 2026-07-09T01:38:00, sem Z).
    Data pura vira meia-noite UTC; datetime sem offset ganha 'Z'."""
    s = (s or "").strip()
    if _DATE_ONLY.match(s):
        return f"{s}T00:00:00Z"
    if _NAIVE_DT.match(s):
        base = s.replace(" ", "T")
        if len(base) == 16:  # sem segundos
            base += ":00"
        return base + "Z"
    return s


def cal_list(token: str, time_min: str, time_max: str, max_results: int,
             calendar_id: str = "primary") -> dict[str, Any]:
    kw: dict[str, Any] = {
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max_results,
    }
    if time_min:
        kw["timeMin"] = _rfc3339(time_min)
    if time_max:
        kw["timeMax"] = _rfc3339(time_max)
    with _Google(token) as g:
        res = g.call("GET", _cal_url(calendar_id, "events"), params=kw)
    return {"events": [_event_out(e) for e in res.get("items", [])]}


def cal_search(token: str, query: str, max_results: int,
               calendar_id: str = "primary") -> dict[str, Any]:
    with _Google(token) as g:
        res = g.call("GET", _cal_url(calendar_id, "events"), params={
            "q": query or "", "singleEvents": True, "orderBy": "startTime", "maxResults": max_results,
        })
    return {"events": [_event_out(e) for e in res.get("items", [])]}


def cal_create(token: str, summary: str, start: str, end: str, description: str = "",
               location: str = "", attendees: str = "",
               calendar_id: str = "primary", tz: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"summary": summary, "start": _when(start, tz), "end": _when(end, tz)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]
    with _Google(token) as g:
        ev = g.call("POST", _cal_url(calendar_id, "events"), body=body)
    return {"ok": True, **_event_out(ev)}


def cal_update(token: str, event_id: str, summary: str = "", start: str = "", end: str = "",
               description: str = "", location: str = "",
               calendar_id: str = "primary", tz: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {}
    if summary:
        body["summary"] = summary
    if start:
        body["start"] = _when(start, tz)
    if end:
        body["end"] = _when(end, tz)
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if not body:
        return {"error": "nothing to update"}
    with _Google(token) as g:
        ev = g.call("PATCH", _cal_url(calendar_id, "events", event_id), body=body)
    return {"ok": True, **_event_out(ev)}


def cal_delete(token: str, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
    with _Google(token) as g:
        g.call("DELETE", _cal_url(calendar_id, "events", event_id))
    return {"ok": True, "id": event_id, "deleted": True}
