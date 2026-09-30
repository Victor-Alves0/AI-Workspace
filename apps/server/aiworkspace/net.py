"""Rede: contexto SSL compartilhado entre todos os clientes httpx.

Cada `httpx.AsyncClient()` monta um `SSLContext` novo, carregando o pacote de CAs do
disco — SÍNCRONO, dentro do event loop: ~400 ms no Windows, dezenas de ms no Linux.
O código cria clientes em 80+ lugares (cada chamada ao modelo, cada busca, cada
integração), e cada criação congelava o servidor inteiro por esse tempo — a
Observabilidade pegou como "travada do event loop" logo antes de cada chamada ao
modelo. Um contexto por configuração (verify/cert/trust_env) resolve: o `SSLContext`
é reutilizável entre clientes e threads.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_cache: dict[tuple, Any] = {}
_installed = False


def install_ssl_cache() -> None:
    """Idempotente. Só cacheia o caso comum (verify=True/caminho, sem certificado de
    cliente); qualquer outra combinação segue o caminho original."""
    global _installed
    if _installed:
        return
    import httpx._transports.default as transports

    original = transports.create_ssl_context

    def cached(verify: Any = True, cert: Any = None, trust_env: bool = True) -> Any:
        if cert is not None or not isinstance(verify, (bool, str)) or verify is False:
            return original(verify=verify, cert=cert, trust_env=trust_env)
        key = (verify, trust_env)
        ctx = _cache.get(key)
        if ctx is None:
            with _lock:
                ctx = _cache.get(key)
                if ctx is None:
                    ctx = original(verify=verify, cert=cert, trust_env=trust_env)
                    _cache[key] = ctx
        return ctx

    transports.create_ssl_context = cached
    _installed = True
    # monta o contexto padrão JÁ (no boot, fora do loop): nem a 1ª chamada trava
    try:
        cached()
    except Exception:  # noqa: BLE001 - sem CAs agora, tenta de novo no 1º uso
        _cache.clear()
