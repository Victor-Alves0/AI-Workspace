"""Google Workspace: OAuth + acesso a Gmail e Agenda (multi-conta).

Divisão de responsabilidades (custo/escala):
  - Credenciais do app OAuth (client id/secret) são GLOBAIS, configuradas na UI
    (admin) e guardadas em `app_settings` — o secret cifrado (Fernet). Nada em env.
  - Cada usuário conecta VÁRIAS contas Google (tabela `google_accounts`); guardamos
    só o *refresh token* de cada uma (cifrado). O access token é buscado/renovado ao
    vivo por conta e NUNCA entra na instância SIFT (senão o índice reconstruiria a
    cada refresh).
  - As chamadas de API (google-api-python-client) são SÍNCRONAS; as tools rodam no
    threadpool de dispatch da SIFT, então blocam apenas um worker, não o event loop.

O `state` do OAuth é assinado com `app_secret` (pyjwt) carregando o user_id — sem
tabela de estado. A troca/refresh do token é feita via httpx (sem google-auth-oauthlib).
"""

from __future__ import annotations

import base64
import logging
import re
import time
from email.message import EmailMessage
from typing import Any

import httpx
import jwt
from sqlalchemy import select
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

OAUTH_SETTING_KEY = "google_oauth"  # em app_settings: {client_id, client_secret_enc}
_STATE_TTL = 600  # 10 min p/ concluir o consentimento

# account_id -> (access_token, expiry_epoch)
_token_cache: dict[str, tuple[str, float]] = {}


# --------------------------------------------------------------------------- #
# Credenciais do app OAuth (globais, na UI) — app_settings
# --------------------------------------------------------------------------- #
async def get_oauth_config(db: AsyncSession) -> dict[str, str] | None:
    """Credenciais do app OAuth (client_id/secret + redirect). None = não configurado."""
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
        "redirect_uri": get_settings().google_redirect_uri,
    }


async def set_oauth_config(db: AsyncSession, client_id: str, client_secret: str | None) -> None:
    """Salva client_id/secret (secret cifrado). `client_secret=None/''` mantém o atual."""
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
        {"sub": user_id, "typ": "google_oauth", "iat": now, "exp": now + _STATE_TTL},
        get_settings().app_secret,
        algorithm="HS256",
    )


def verify_state(state: str) -> str | None:
    try:
        data = jwt.decode(state, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "google_oauth":
        return None
    return data.get("sub")


# --------------------------------------------------------------------------- #
# Fluxo OAuth (credenciais passadas explicitamente)
# --------------------------------------------------------------------------- #
def authorization_url(user_id: str, creds: dict[str, str]) -> str:
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": creds["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",       # queremos refresh token
        "prompt": "consent",            # força o refresh token mesmo em re-consentimento
        "include_granted_scopes": "true",
        "state": sign_state(user_id),
    }
    return str(httpx.URL(AUTH_URI, params=params))


async def exchange_code(code: str, creds: dict[str, str]) -> dict[str, Any]:
    """Troca o `code` por tokens e resolve o e-mail da conta.

    Retorna {refresh_token, email, scopes} ou {error}."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URI,
            data={
                "code": code,
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "redirect_uri": creds["redirect_uri"],
                "grant_type": "authorization_code",
            },
        )
        if r.status_code != 200:
            return {"error": f"token exchange failed: {r.text[:300]}"}
        tok = r.json()
        refresh = tok.get("refresh_token")
        access = tok.get("access_token")
        if not refresh:
            # sem refresh token não conseguimos agir depois (usuário já consentiu antes
            # sem revogar): peça pra remover o acesso em myaccount.google.com e reconectar.
            return {"error": "no_refresh_token"}
        email = ""
        if access:
            ui = await client.get(USERINFO_URI, headers={"Authorization": f"Bearer {access}"})
            if ui.status_code == 200:
                email = ui.json().get("email", "")
    return {"refresh_token": refresh, "email": email, "scopes": tok.get("scope", "")}


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
    r.raise_for_status()
    tok = r.json()
    return tok["access_token"], time.time() + int(tok.get("expires_in", 3600))


async def get_access_token(account_id: str) -> str | None:
    """Access token válido p/ uma conta conectada (renova se preciso).

    None = conta inexistente, app não configurado, ou refresh revogado/expirado.
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
            refresh = acc.refresh_token if acc else None
            creds = await get_oauth_config(db)
    finally:
        await eng.dispose()
    if not refresh or not creds:
        return None
    try:
        token, exp = await _refresh_access_token(refresh, creds)
    except Exception as exc:  # noqa: BLE001 - token revogado/expirado (modo teste 7 dias)
        logger.warning("Refresh do token Google falhou (conta %s): %s", account_id, exc)
        return None
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
def _service(token: str, api: str, version: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(token=token)
    return build(api, version, credentials=creds, cache_discovery=False)


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
    """Lista mensagens do Gmail (metadados + snippet).

    ``max_results``: quantas puxar (metadados; teto ``GMAIL_METADATA_MAX``).
    ``0`` (ou negativo) = CONTAR todas que casam a ``query`` (ids paginados até
    ``GMAIL_SEARCH_HARD_MAX``) e devolver metadados só das primeiras
    ``GMAIL_COUNT_SAMPLE`` — `count` é a contagem real."""
    svc = _service(token, "gmail", "v1")
    count_all = max_results <= 0
    want_ids = GMAIL_SEARCH_HARD_MAX if count_all else min(int(max_results), GMAIL_METADATA_MAX)
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < want_ids:
        res = svc.users().messages().list(
            userId="me", q=query or "",
            maxResults=min(500, want_ids - len(ids)),
            pageToken=page_token,
        ).execute()
        ids.extend(m["id"] for m in res.get("messages", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    meta_n = min(len(ids), GMAIL_COUNT_SAMPLE if count_all else want_ids)
    out = []
    for mid in ids[:meta_n]:
        full = svc.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
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
    svc = _service(token, "gmail", "v1")
    full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
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
    svc = _service(token, "gmail", "v1")
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
    sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"ok": True, "id": sent.get("id")}


_MODIFY = {
    "archive": {"removeLabelIds": ["INBOX"]},
    "read": {"removeLabelIds": ["UNREAD"]},
    "unread": {"addLabelIds": ["UNREAD"]},
    "star": {"addLabelIds": ["STARRED"]},
    "unstar": {"removeLabelIds": ["STARRED"]},
}


def gmail_modify(token: str, msg_id: str, action: str) -> dict[str, Any]:
    svc = _service(token, "gmail", "v1")
    if action == "trash":
        svc.users().messages().trash(userId="me", id=msg_id).execute()
        return {"ok": True, "id": msg_id, "action": "trash"}
    mod = _MODIFY.get(action)
    if mod is None:
        return {"error": f"unknown action '{action}' (use archive/trash/read/unread/star/unstar)"}
    svc.users().messages().modify(userId="me", id=msg_id, body=mod).execute()
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
    svc = _service(token, "calendar", "v3")
    kw: dict[str, Any] = {
        "calendarId": calendar_id or "primary",
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max_results,
    }
    if time_min:
        kw["timeMin"] = _rfc3339(time_min)
    if time_max:
        kw["timeMax"] = _rfc3339(time_max)
    res = svc.events().list(**kw).execute()
    return {"events": [_event_out(e) for e in res.get("items", [])]}


def cal_search(token: str, query: str, max_results: int,
               calendar_id: str = "primary") -> dict[str, Any]:
    svc = _service(token, "calendar", "v3")
    res = svc.events().list(
        calendarId=calendar_id or "primary", q=query or "",
        singleEvents=True, orderBy="startTime", maxResults=max_results,
    ).execute()
    return {"events": [_event_out(e) for e in res.get("items", [])]}


def cal_create(token: str, summary: str, start: str, end: str, description: str = "",
               location: str = "", attendees: str = "",
               calendar_id: str = "primary", tz: str = "") -> dict[str, Any]:
    svc = _service(token, "calendar", "v3")
    body: dict[str, Any] = {"summary": summary, "start": _when(start, tz), "end": _when(end, tz)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e.strip()} for e in attendees.split(",") if e.strip()]
    ev = svc.events().insert(calendarId=calendar_id or "primary", body=body).execute()
    return {"ok": True, **_event_out(ev)}


def cal_update(token: str, event_id: str, summary: str = "", start: str = "", end: str = "",
               description: str = "", location: str = "",
               calendar_id: str = "primary", tz: str = "") -> dict[str, Any]:
    svc = _service(token, "calendar", "v3")
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
    ev = svc.events().patch(
        calendarId=calendar_id or "primary", eventId=event_id, body=body
    ).execute()
    return {"ok": True, **_event_out(ev)}


def cal_delete(token: str, event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
    svc = _service(token, "calendar", "v3")
    svc.events().delete(calendarId=calendar_id or "primary", eventId=event_id).execute()
    return {"ok": True, "id": event_id, "deleted": True}
