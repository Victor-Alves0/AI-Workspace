"""Auto-instrumentação do SQLAlchemy (e helper para httpx).

Cada query executada é cronometrada e atribuída ao **span corrente**: conta como
leitura ou escrita e soma o tempo de banco daquele span — é isto que responde
"quantas leituras/escritas e quanto tempo de banco esta chamada gastou". Guarda
também o SQL NORMALIZADO (sem valores literais) das primeiras queries do span, para
você ver o que rodou sem expor dado.

Como o span corrente chega ao handler síncrono do SQLAlchemy: o bridge greenlet do
SQLAlchemy 2.0 copia os context vars ao entrar na camada síncrona, então o
`current_span()` visto aqui é o mesmo do código async que disparou a query.
"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from .context import current_span, current_trace

# nº máx. de statements (normalizados) guardados por span — o resto vira só contagem
_MAX_QUERIES_PER_SPAN = 25

_WRITE_RE = re.compile(r"^\s*(insert|update|delete|create|alter|drop|truncate)\b", re.I)
_WS_RE = re.compile(r"\s+")
_STR_RE = re.compile(r"'(?:[^']|'')*'")
_NUM_RE = re.compile(r"\b\d+\b")
# parâmetros posicionais/nomeados ($1, :id, %(x)s, ?) já não são literais; mantemos


def _is_write(sql: str) -> bool:
    return bool(_WRITE_RE.match(sql or ""))


def normalize_sql(sql: str, limit: int = 300) -> str:
    """SQL sem valores: colapsa espaços e troca literais por '?'. Nunca vaza dado
    (os valores reais nem chegam aqui — vêm em parâmetros separados)."""
    s = _WS_RE.sub(" ", sql or "").strip()
    s = _STR_RE.sub("?", s)
    s = _NUM_RE.sub("?", s)
    return s[:limit]


def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    stack = conn.info.setdefault("_obs_t0", [])
    # `after_cursor_execute` NÃO dispara quando a query falha, então a marca do
    # `before` correspondente ficaria órfã na conexão do pool para sempre. O pop é
    # LIFO (a medição continua correta), mas sem esta poda a lista cresce a cada
    # query com erro. Consultas aninhadas de verdade nunca passam de um punhado.
    if len(stack) > 8:
        del stack[:-8]
    stack.append(time.perf_counter())


def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    stack = conn.info.get("_obs_t0")
    if not stack:
        return
    elapsed_ms = (time.perf_counter() - stack.pop()) * 1000
    write = _is_write(statement)
    sp = current_span()
    if sp is None:
        # sem span aberto: query feita direto no handler da rota. Atribui ao trace
        # (senão sumiria da contabilidade). Sem trace também: nada a fazer.
        tr = current_trace()
        if tr is not None:
            try:
                tr.add_root_db(write=write, ms=elapsed_ms)
            except Exception:  # noqa: BLE001
                pass
        return
    try:
        sp.add_db(write=write, ms=elapsed_ms)
        queries = sp.attrs.get("queries")
        if queries is None:
            queries = []
            sp.attrs["queries"] = queries
        if len(queries) < _MAX_QUERIES_PER_SPAN:
            rows = getattr(cursor, "rowcount", -1)
            queries.append({
                "sql": normalize_sql(statement),
                "ms": round(elapsed_ms, 2),
                "rows": rows if isinstance(rows, int) and rows >= 0 else None,
            })
    except Exception:  # noqa: BLE001 - instrumentação nunca quebra a query
        pass


_installed = False


def install(engine: Engine) -> None:
    """Anexa os listeners ao engine SÍNCRONO (o async expõe `.sync_engine`).
    Idempotente."""
    global _installed
    if _installed:
        return
    event.listen(engine, "before_cursor_execute", _before)
    event.listen(engine, "after_cursor_execute", _after)
    _installed = True


# --------------------------------------------------------------------------- #
# httpx: tempo de conexão vs resposta numa chamada externa
# --------------------------------------------------------------------------- #

def httpx_event_hooks() -> dict[str, list]:
    """Hooks para passar a `httpx.AsyncClient(event_hooks=...)`: cronometram a
    requisição externa e somam ao span HTTP corrente. Uso opcional — os pontos
    quentes (provedores de IA) já são envolvidos por um span explícito."""

    async def _on_request(request):  # noqa: ANN001
        request.extensions["_obs_t0"] = time.perf_counter()

    async def _on_response(response):  # noqa: ANN001
        t0 = response.request.extensions.get("_obs_t0")
        if t0 is None:
            return
        ms = (time.perf_counter() - t0) * 1000
        sp = current_span()
        if sp is not None:
            sp.http_ms += ms
            sp.set(http_status=response.status_code, http_host=response.request.url.host)

    return {"request": [_on_request], "response": [_on_response]}
