"""OpenRouter: "Conectar" por OAuth PKCE, em vez de colar a chave de API.

O usuário clica, autoriza no site do OpenRouter e volta com uma chave já criada —
a mesma chave que ele colaria à mão, só que emitida pelo fluxo. É PKCE puro: não
existe client secret, e o `callback_url` não precisa ser registrado em lugar nenhum
(localhost em qualquer porta é aceito), então funciona igual no desktop, no Docker
local e numa VPS — sem nenhum cadastro prévio.

Duas decisões que valem explicar:

- **`state` no CAMINHO, não na query.** A URL de autorização do OpenRouter não tem
  parâmetro `state`; o que volta é só o `code`. Então o `state` assinado viaja
  dentro do próprio `callback_url` (`.../callback/<state>`). No caminho, e não como
  query, porque não temos garantia de como o OpenRouter concatena o `code` à URL de
  retorno (`?` vs `&`) — um segmento de caminho sobrevive aos dois.

- **`code_verifier` DERIVADO, não guardado.** Em vez de manter o verifier numa
  tabela ou num dicionário em memória (que um restart do servidor esvaziaria no meio
  do fluxo), ele é derivado por HMAC do `APP_SECRET` com o `jti` do state. Só o
  servidor consegue reconstruí-lo, ele nunca trafega, e o fluxo é retomável depois
  de um restart. Mesmo espírito do `state` assinado do google_service: identidade
  vem da assinatura, não de estado no processo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid

import httpx
import jwt

from ..config import get_settings

logger = logging.getLogger(__name__)

AUTH_URI = "https://openrouter.ai/auth"
KEYS_URI = "https://openrouter.ai/api/v1/auth/keys"

_STATE_TTL = 600  # 10 min: tempo de dar a volta pelo site do OpenRouter


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign_state(user_id: str) -> str:
    """State assinado com um `jti` aleatório — o `jti` é a semente do verifier."""
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "typ": "openrouter_pkce",
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + _STATE_TTL,
        },
        get_settings().app_secret,
        algorithm="HS256",
    )


def verify_state(state: str) -> tuple[str, str] | None:
    """(user_id, jti) ou None se inválido/expirado/de outro fluxo."""
    try:
        data = jwt.decode(state, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "openrouter_pkce":
        return None
    sub, jti = data.get("sub"), data.get("jti")
    if not sub or not jti:
        return None
    return str(sub), str(jti)


def code_verifier(jti: str) -> str:
    """Verifier PKCE derivado do APP_SECRET + `jti`. 43 chars base64url (o mínimo da RFC)."""
    mac = hmac.new(
        get_settings().app_secret.encode(), f"openrouter-pkce:{jti}".encode(), hashlib.sha256
    ).digest()
    return _b64url(mac)


def _challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def callback_url(state: str) -> str:
    return f"{get_settings().openrouter_redirect_uri.rstrip('/')}/{state}"


def authorization_url(user_id: str) -> str:
    state = sign_state(user_id)
    _, jti = verify_state(state)  # type: ignore[misc]  # acabamos de assinar
    params = {
        "callback_url": callback_url(state),
        "code_challenge": _challenge(code_verifier(jti)),
        "code_challenge_method": "S256",
    }
    return str(httpx.URL(AUTH_URI, params=params))


async def exchange_code(code: str, jti: str) -> dict[str, str]:
    """Troca o `code` pela chave de API do usuário. {"key": ...} ou {"error": ...}."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                KEYS_URI,
                json={
                    "code": code,
                    "code_verifier": code_verifier(jti),
                    "code_challenge_method": "S256",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("openrouter pkce: falha de rede: %s", exc)
        return {"error": "network"}
    if r.status_code != 200:
        logger.warning("openrouter pkce: troca falhou (%s): %s", r.status_code, r.text[:300])
        return {"error": f"exchange_failed_{r.status_code}"}
    key = ""
    try:
        key = (r.json() or {}).get("key") or ""
    except ValueError:
        pass
    if not key:
        return {"error": "empty_key"}
    return {"key": key}
