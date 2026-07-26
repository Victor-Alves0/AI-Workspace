"""Notion: conexão por-usuário (token de integração interna OU OAuth) + acesso à API.

Espelha o [[github_service.py]] na divisão de responsabilidades:
  - Credenciais do OAuth (client id/secret) são GLOBAIS, na UI, em `app_settings`
    (secret cifrado). Só p/ quem quiser o fluxo "Conectar" com botão.
  - Cada usuário conecta VÁRIAS contas (tabela `notion_accounts`): um token de
    integração interna colado (`auth_type="token"`) OU um bot token OAuth. O token é
    cifrado e NUNCA entra na instância SIFT — é lido ao vivo por conta.
  - As chamadas de API são SÍNCRONAS (httpx.Client); as tools rodam no threadpool de
    dispatch da SIFT, então blocam só um worker, não o event loop.

O token do Notion não expira nem tem refresh (nem no OAuth), então `get_token` só
devolve o token guardado. O `state` do OAuth é assinado com `app_secret` (pyjwt).
"""

from __future__ import annotations

import base64
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

AUTH_URI = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URI = "https://api.notion.com/v1/oauth/token"
API_BASE = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"

OAUTH_SETTING_KEY = "notion_oauth"  # em app_settings: {client_id, client_secret_enc}
_STATE_TTL = 600


class NotionError(Exception):
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
    except Exception:  # noqa: BLE001 - APP_SECRET trocado invalida o ciphertext
        return None
    return {
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uri": get_settings().notion_redirect_uri,
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
        {"sub": user_id, "typ": "notion_oauth", "iat": now, "exp": now + _STATE_TTL},
        get_settings().app_secret,
        algorithm="HS256",
    )


def verify_state(state: str) -> str | None:
    try:
        data = jwt.decode(state, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "notion_oauth":
        return None
    return data.get("sub")


# --------------------------------------------------------------------------- #
# Fluxo OAuth
# --------------------------------------------------------------------------- #
def authorization_url(user_id: str, creds: dict[str, str]) -> str:
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": creds["redirect_uri"],
        "response_type": "code",
        "owner": "user",
        "state": sign_state(user_id),
    }
    return str(httpx.URL(AUTH_URI, params=params))


async def exchange_code(code: str, creds: dict[str, str]) -> dict[str, Any]:
    """Troca o `code` por um bot token. O Notion autentica a troca com Basic
    (client_id:client_secret). Retorna {access_token, workspace, bot_id, avatar_url}
    ou {error}. O token não expira e não tem refresh."""
    basic = base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            TOKEN_URI,
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json"},
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": creds["redirect_uri"],
            },
        )
    if r.status_code != 200:
        try:
            msg = r.json().get("error_description") or r.json().get("error") or r.text[:300]
        except Exception:  # noqa: BLE001
            msg = r.text[:300]
        return {"error": f"token exchange failed: {msg}"}
    tok = r.json()
    access = tok.get("access_token")
    if not access:
        return {"error": tok.get("error") or "no_access_token"}
    return {
        "access_token": access,
        "workspace": tok.get("workspace_name") or "",
        "bot_id": tok.get("bot_id") or "",
        "avatar_url": tok.get("workspace_icon") or "",
    }


async def validate_token(token: str) -> dict[str, Any]:
    """Valida um token de integração interna via GET /users/me. Retorna
    {workspace, bot_id, avatar_url} ou {error}."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{API_BASE}/users/me",
                headers={"Authorization": f"Bearer {token.strip()}", "Notion-Version": API_VERSION},
            )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if r.status_code != 200:
        return {"error": f"token inválido (HTTP {r.status_code})"}
    j = r.json()
    bot = j.get("bot") or {}
    ws = bot.get("workspace_name") or j.get("name") or ""
    return {
        "workspace": ws,
        "bot_id": j.get("id") or "",
        "avatar_url": j.get("avatar_url") or "",
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

    from ..models import NotionAccount

    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            acc = await db.get(NotionAccount, _as_uuid(account_id))
            return acc.token if acc else None
    finally:
        await eng.dispose()


def forget(account_id: str) -> None:  # simetria com github_service (sem cache aqui)
    return None


async def test_account(account_id: str) -> dict[str, Any]:
    token = await get_token(account_id)
    if not token:
        return {"ok": False}
    info = await validate_token(token)
    if info.get("error"):
        return {"ok": False}
    return {"ok": True, "workspace": info.get("workspace", "")}


# --------------------------------------------------------------------------- #
# API (SÍNCRONO — roda no threadpool de dispatch da SIFT)
# --------------------------------------------------------------------------- #
def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def _request(token: str, method: str, path: str, *, params: dict | None = None,
             json: dict | None = None) -> Any:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    try:
        with httpx.Client(timeout=30) as client:
            r = client.request(method, url, headers=_headers(token), params=params, json=json)
    except Exception as exc:  # noqa: BLE001
        raise NotionError(f"falha de rede: {exc}") from exc
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {}
    if r.status_code >= 400:
        msg = (data.get("message") if isinstance(data, dict) else None) or f"HTTP {r.status_code}"
        raise NotionError(msg)
    return data


def _rich_text(arr: Any) -> str:
    if not isinstance(arr, list):
        return ""
    return "".join(str(rt.get("plain_text", "")) for rt in arr if isinstance(rt, dict))


def _title_of(obj: dict) -> str:
    """Título de um resultado de busca (página OU database)."""
    if obj.get("object") == "database":
        return _rich_text(obj.get("title")) or "(sem título)"
    props = obj.get("properties") or {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return _rich_text(prop.get("title")) or "(sem título)"
    return "(sem título)"


def _hint(obj: dict) -> str:
    return f"{obj.get('object', 'page')} · {obj.get('id', '')}"


def search(token: str, query: str = "", kind: str = "", limit: int = 10) -> list[dict]:
    """Busca páginas/databases acessíveis à integração. `kind`: page|database|''."""
    body: dict[str, Any] = {"page_size": min(max(1, limit), 100)}
    if query.strip():
        body["query"] = query.strip()
    if kind in ("page", "database"):
        body["filter"] = {"property": "object", "value": kind}
    data = _request(token, "POST", "/search", json=body)
    out = []
    for r in (data.get("results") or []):
        if not isinstance(r, dict):
            continue
        out.append({
            "id": r.get("id"),
            "type": r.get("object"),
            "title": _title_of(r),
            "url": r.get("url", ""),
            "last_edited": r.get("last_edited_time", ""),
        })
    return out


def _blocks_text(token: str, block_id: str, depth: int = 0, max_depth: int = 2) -> list[str]:
    """Achata o conteúdo de blocos de uma página em linhas de texto legível."""
    if depth > max_depth:
        return []
    try:
        data = _request(token, "GET", f"/blocks/{block_id}/children", params={"page_size": 100})
    except NotionError:
        return []
    lines: list[str] = []
    indent = "  " * depth
    for b in (data.get("results") or []):
        if not isinstance(b, dict):
            continue
        bt = b.get("type", "")
        payload = b.get(bt) or {}
        text = _rich_text(payload.get("rich_text"))
        if bt.startswith("heading_"):
            level = bt.split("_")[-1]
            lines.append(f"{indent}{'#' * (int(level) if level.isdigit() else 2)} {text}")
        elif bt == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif bt == "numbered_list_item":
            lines.append(f"{indent}1. {text}")
        elif bt == "to_do":
            checked = "x" if payload.get("checked") else " "
            lines.append(f"{indent}- [{checked}] {text}")
        elif bt == "quote":
            lines.append(f"{indent}> {text}")
        elif bt == "code":
            lines.append(f"{indent}```{payload.get('language', '')}\n{text}\n{indent}```")
        elif bt in ("paragraph", "callout", "toggle"):
            if text:
                lines.append(f"{indent}{text}")
        elif bt == "child_page":
            lines.append(f"{indent}[página: {payload.get('title', '')}]")
        elif bt == "divider":
            lines.append(f"{indent}---")
        if b.get("has_children") and bt not in ("child_page", "child_database"):
            lines.extend(_blocks_text(token, b["id"], depth + 1, max_depth))
    return lines


def get_page(token: str, page_id: str) -> dict:
    page = _request(token, "GET", f"/pages/{page_id}")
    title = _title_of(page)
    text = "\n".join(_blocks_text(token, page_id))
    return {
        "id": page.get("id"),
        "title": title,
        "url": page.get("url", ""),
        "last_edited": page.get("last_edited_time", ""),
        "content": text,
    }


def _text_to_blocks(content: str) -> list[dict]:
    """Converte texto (markdown leve) em blocos do Notion. Linhas '# '→heading_2,
    '## '→heading_3, '- '→bullet, '1. '→numbered; senão parágrafo."""
    blocks: list[dict] = []

    def rt(t: str) -> list[dict]:
        return [{"type": "text", "text": {"content": t[:2000]}}]

    for raw in (content or "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": rt(line[3:])}})
        elif line.startswith("# "):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": rt(line[2:])}})
        elif line.startswith(("- ", "* ")):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(line[2:])}})
        elif len(line) > 3 and line[0].isdigit() and line[1:3] == ". ":
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt(line[3:])}})
        else:
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": rt(line)}})
    return blocks[:100]


def _db_title_prop(token: str, database_id: str) -> str:
    """Nome da propriedade de título de um database (varia por database)."""
    db = _request(token, "GET", f"/databases/{database_id}")
    for name, prop in (db.get("properties") or {}).items():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return name
    return "Name"


def create_page(token: str, title: str, content: str = "",
                parent_page_id: str = "", database_id: str = "") -> dict:
    """Cria uma página. Pai = uma página existente (parent_page_id) OU um database."""
    children = _text_to_blocks(content)
    title_rt = [{"type": "text", "text": {"content": (title or "Sem título")[:2000]}}]
    if database_id.strip():
        prop_name = _db_title_prop(token, database_id.strip())
        body = {
            "parent": {"database_id": database_id.strip()},
            "properties": {prop_name: {"title": title_rt}},
        }
    elif parent_page_id.strip():
        body = {
            "parent": {"page_id": parent_page_id.strip()},
            "properties": {"title": {"title": title_rt}},
        }
    else:
        raise NotionError("informe parent_page_id (página) ou database_id como pai")
    if children:
        body["children"] = children
    page = _request(token, "POST", "/pages", json=body)
    return {"ok": True, "id": page.get("id"), "url": page.get("url", "")}


def append_blocks(token: str, page_id: str, content: str) -> dict:
    children = _text_to_blocks(content)
    if not children:
        raise NotionError("nada para adicionar (content vazio)")
    _request(token, "PATCH", f"/blocks/{page_id.strip()}/children", json={"children": children})
    return {"ok": True, "id": page_id.strip()}


def query_database(token: str, database_id: str, limit: int = 20) -> list[dict]:
    data = _request(token, "POST", f"/databases/{database_id.strip()}/query",
                    json={"page_size": min(max(1, limit), 100)})
    out = []
    for r in (data.get("results") or []):
        if not isinstance(r, dict):
            continue
        out.append({
            "id": r.get("id"),
            "title": _title_of(r),
            "url": r.get("url", ""),
            "last_edited": r.get("last_edited_time", ""),
        })
    return out
