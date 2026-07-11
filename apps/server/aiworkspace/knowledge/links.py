"""URLs assinadas p/ baixar o documento original de uma fonte citada.

Mesmo padrão do `providers/image_gen` (pyjwt + app_secret): o token codifica só o
id do doc (URL-capacidade), então funciona num link/`<a href>` sem cookie. Módulo
isolado p/ o orchestrator montar as fontes sem importar `knowledge_routes`.
"""

from __future__ import annotations

import time

import jwt

from ..config import get_settings


def sign_doc_url(doc_id: str, ttl_days: int = 3650) -> str:
    now = int(time.time())
    tok = jwt.encode(
        {"doc": doc_id, "iat": now, "exp": now + ttl_days * 86400},
        get_settings().app_secret,
        algorithm="HS256",
    )
    return f"/knowledge/docs/{doc_id}/raw?t={tok}"


def verify_doc_token(doc_id: str, token: str) -> bool:
    try:
        data = jwt.decode(token, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return data.get("doc") == doc_id
