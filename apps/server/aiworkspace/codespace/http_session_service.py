"""Sessão HTTP COM ESTADO por chat (cookie jar + headers persistentes + histórico).

Motivação (harness): sem isto, o agente falava com um serviço rodando (a própria app em
dev, uma API, um alvo) via requisições de UM TIRO e gerenciava cookie NA MÃO — copiava o
`Set-Cookie` de volta pro header a cada chamada, re-autenticava todo turno, e não tinha
histórico pra comparar. Uma `httpx.Client` viva por chat resolve: faz login UMA vez e
segue autenticado, guarda um histórico consultável, e aceita headers persistentes (ex.:
um Bearer). Agnóstico de domínio: testar sua própria API em dev, checar integração, ou
sondar um alvo. Roda DE DENTRO do server (alcança 127.0.0.1/preview em qualquer ambiente).

Registro em memória por-processo (como previews/exec_jobs) — não sobrevive a restart.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

import httpx

_BODY_CAP = 200_000        # corta o corpo devolvido ao modelo
_HISTORY = 60              # requisições guardadas por sessão
_MAX_SESSIONS = 64         # teto global de sessões vivas


class _Session:
    def __init__(self) -> None:
        # cookie jar + conexões reaproveitadas moram na Client; follow_redirects=False
        # p/ o modelo VER os 302 (fluxo de login/segurança). `follow` por chamada libera.
        self.client = httpx.Client(follow_redirects=False, timeout=30)
        self.headers: dict[str, str] = {}     # headers persistentes (ex.: Authorization)
        self.history: deque[dict] = deque(maxlen=_HISTORY)
        self.lock = threading.Lock()
        self.last_used = time.monotonic()     # p/ eviction LRU (não descartar sessão ativa)


_sessions: dict[str, _Session] = {}
_reg = threading.Lock()


def _get(chat_id: str, create: bool = True) -> _Session | None:
    with _reg:
        s = _sessions.get(chat_id)
        if s is not None:
            s.last_used = time.monotonic()   # "toca" p/ o LRU (sessão em uso não é despejada)
            return s
        if not create:
            return None
        if len(_sessions) >= _MAX_SESSIONS:
            # despeja a MENOS RECENTEMENTE USADA (LRU) — não a sessão de um chat ativo
            lru_id = min(_sessions, key=lambda k: _sessions[k].last_used)
            try:
                _sessions[lru_id].client.close()
            except Exception:  # noqa: BLE001
                pass
            _sessions.pop(lru_id, None)
        s = _Session()
        _sessions[chat_id] = s
        return s


def request(chat_id: str, method: str, url: str, *, headers: dict | None = None,
            body: Any = None, follow: bool = False, timeout: float = 30) -> dict[str, Any]:
    if not (url or "").strip():
        return {"error": "informe a url (ex.: http://127.0.0.1:4001/api/health)"}
    s = _get(chat_id)
    assert s is not None
    hdrs = {**s.headers, **(headers or {})}
    content = body.encode("utf-8") if isinstance(body, str) else body
    try:
        with s.lock:
            t0 = time.monotonic()
            r = s.client.request(
                (method or "GET").upper(), url.strip(), headers=hdrs or None,
                content=content, follow_redirects=bool(follow),
                timeout=min(float(timeout or 30), 120),
            )
            ms = round((time.monotonic() - t0) * 1000)
    except httpx.HTTPError as exc:
        return {"error": f"falha na requisição: {str(exc)[:300]}"}
    raw = r.content or b""
    truncated = len(raw) > _BODY_CAP
    text = raw[:_BODY_CAP].decode(r.encoding or "utf-8", "replace")
    s.history.append({"method": (method or "GET").upper(), "url": str(r.request.url),
                      "status": r.status_code, "ms": ms})
    return {
        "http_status": r.status_code,
        "resp_headers": {k: v for k, v in r.headers.items()},
        "body": text, "truncated": truncated, "url": str(r.request.url),
        "elapsed_ms": ms,
        # o estado que importa: o que a sessão carrega agora (login persiste sozinho)
        "cookies": {k: v for k, v in s.client.cookies.items()},
    }


def history(chat_id: str, limit: int = 20) -> dict[str, Any]:
    s = _get(chat_id, create=False)
    if s is None:
        return {"history": []}
    return {"history": list(s.history)[-int(limit or 20):]}


def set_headers(chat_id: str, headers: dict | None) -> dict[str, Any]:
    s = _get(chat_id)
    assert s is not None
    if headers:
        s.headers.update({str(k): str(v) for k, v in headers.items()})
    return {"ok": True, "headers": dict(s.headers)}


def reset(chat_id: str) -> dict[str, Any]:
    with _reg:
        s = _sessions.pop(chat_id, None)
    if s is not None:
        try:
            s.client.close()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "reset": True}
