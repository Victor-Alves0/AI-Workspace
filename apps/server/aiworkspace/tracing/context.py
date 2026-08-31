"""O modelo trace/span e a propagação por context vars.

Um `Trace` é a raiz; `Span`s formam uma árvore por baixo dele via `parent_id`. O
span corrente vive num `ContextVar`, então abrir um span dentro de outro aninha
automaticamente — inclusive através de `await` e do bridge greenlet do SQLAlchemy,
que copiam o contexto.

Nada aqui toca o banco. Ao fechar, trace e spans são entregues ao `sink`, que os
grava em lote e em background.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

# limites de segurança: telemetria nunca pode crescer sem teto na memória de um
# único trace (um loop de tools patológico geraria milhares de spans)
_MAX_SPANS_PER_TRACE = 2000
_MAX_ATTR_LEN = 2000

_current_trace: ContextVar["Trace | None"] = ContextVar("obs_current_trace", default=None)
_current_span: ContextVar["Span | None"] = ContextVar("obs_current_span", default=None)
# Traces de longa duração ainda não chegaram ao sink. Permite que um beacon do
# browser (normalmente emitido no primeiro token) seja anexado antes do ``done``.
# É somente um índice local/best-effort; o trace continua sendo a fonte persistida.
_open_traces: dict[str, "Trace"] = {}


def _now() -> float:
    return time.perf_counter()


def _clip(value: Any) -> Any:
    """Corta strings longas — um atributo não pode virar um dump de conteúdo."""
    if isinstance(value, str) and len(value) > _MAX_ATTR_LEN:
        return value[:_MAX_ATTR_LEN] + "…"
    return value


@dataclass
class Span:
    """Uma etapa dentro de um trace. Durações em milissegundos."""

    id: str
    trace_id: str
    parent_id: str | None
    name: str
    kind: str  # internal | db | http | llm | tool | rag | memory | auth | client
    started_wall: float  # epoch (para ordenar/exibir)
    _t0: float = field(repr=False)  # perf_counter no início
    duration_ms: float = 0.0
    status: str = "ok"  # ok | error
    error: str = ""
    # agregados de banco DESTE span (não dos filhos): a "leitura/escrita da chamada"
    db_reads: int = 0
    db_writes: int = 0
    db_ms: float = 0.0
    http_ms: float = 0.0
    attrs: dict[str, Any] = field(default_factory=dict)

    def set(self, **kv: Any) -> None:
        for k, v in kv.items():
            if v is not None:
                self.attrs[k] = _clip(v)

    def add_db(self, *, write: bool, ms: float) -> None:
        if write:
            self.db_writes += 1
        else:
            self.db_reads += 1
        self.db_ms += ms

    def close(self, error: str = "") -> None:
        self.duration_ms = round((_now() - self._t0) * 1000, 3)
        if error:
            self.status = "error"
            self.error = _clip(error)


@dataclass
class Trace:
    """Operação ponta-a-ponta. Guarda a lista de spans até o flush."""

    id: str
    name: str
    kind: str  # http | chat | api | automation | channel | worker
    started_wall: float
    _t0: float = field(repr=False)
    user_id: str | None = None
    method: str = ""
    path: str = ""
    status_code: int = 0
    duration_ms: float = 0.0
    status: str = "ok"
    error: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)
    dropped_spans: int = 0
    # trabalho de banco feito FORA de qualquer span (direto no handler da rota): o
    # instrument cai aqui quando não há span aberto, senão essas queries sumiriam
    root_db_reads: int = 0
    root_db_writes: int = 0
    root_db_ms: float = 0.0

    def set(self, **kv: Any) -> None:
        for k, v in kv.items():
            if v is not None:
                self.attrs[k] = _clip(v)

    def add_root_db(self, *, write: bool, ms: float) -> None:
        if write:
            self.root_db_writes += 1
        else:
            self.root_db_reads += 1
        self.root_db_ms += ms

    def add_span(self, sp: Span) -> bool:
        if len(self.spans) >= _MAX_SPANS_PER_TRACE:
            self.dropped_spans += 1
            return False
        self.spans.append(sp)
        return True

    def close(self, error: str = "") -> None:
        self.duration_ms = round((_now() - self._t0) * 1000, 3)
        if error:
            self.status = "error"
            self.error = error[:_MAX_ATTR_LEN]


# --------------------------------------------------------------------------- #
# Acesso ao contexto corrente
# --------------------------------------------------------------------------- #

def current_trace() -> Trace | None:
    return _current_trace.get()


def current_span() -> Span | None:
    return _current_span.get()


def trace_id_of_current() -> str | None:
    t = _current_trace.get()
    return t.id if t is not None else None


def get_open_trace(trace_id: str) -> Trace | None:
    """Devolve um trace ainda em execução, se ele pertence a este processo."""
    return _open_traces.get(trace_id)


def set_trace_user(user_id: str | None) -> None:
    """Amarra o trace corrente a um usuário (chamado quando a auth resolve, já
    depois do middleware ter aberto o trace sem saber quem era)."""
    tr = _current_trace.get()
    if tr is not None and user_id and not tr.user_id:
        tr.user_id = str(user_id)


def annotate(**kv: Any) -> None:
    """Anota o span corrente (ou o trace, se não houver span aberto)."""
    sp = _current_span.get()
    if sp is not None:
        sp.set(**kv)
        return
    tr = _current_trace.get()
    if tr is not None:
        tr.set(**kv)


def record_error(exc: BaseException) -> None:
    """Marca o span corrente e o trace como erro. Não levanta."""
    msg = f"{type(exc).__name__}: {exc}"
    sp = _current_span.get()
    if sp is not None:
        sp.status = "error"
        sp.error = msg[:_MAX_ATTR_LEN]
    tr = _current_trace.get()
    if tr is not None and tr.status != "error":
        tr.status = "error"
        tr.error = msg[:_MAX_ATTR_LEN]


def new_trace(name: str, *, kind: str, user_id: str | None = None,
              method: str = "", path: str = "", **attrs: Any) -> Trace:
    """Cria um trace ainda *desanexado* do contexto atual.

    Gerações de chat continuam depois que a request SSE devolve os headers. Elas
    não podem herdar o trace HTTP já fechado; por isso o driver cria este trace
    antes de entrar na task e o ativa somente durante a sua vida inteira.
    """
    tr = Trace(id=uuid.uuid4().hex, name=name[:200], kind=kind,
               started_wall=time.time(), _t0=_now(), user_id=user_id,
               method=method, path=path[:300])
    tr.set(**attrs)
    return tr


@contextmanager
def activate_trace(tr: Trace) -> Iterator[Trace]:
    """Ativa e fecha um trace previamente criado, mesmo sob outro trace.

    Diferente de :func:`start_trace`, isto troca deliberadamente o contexto.
    É apropriado para trabalho destacado (``asyncio.Task``) cujo ciclo de vida
    não coincide com a request que o iniciou.
    """
    tok_t = _current_trace.set(tr)
    tok_s = _current_span.set(None)
    _open_traces[tr.id] = tr
    err = ""
    try:
        yield tr
    except BaseException as exc:  # noqa: BLE001 - espelha start_trace
        err = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        tr.close(err)
        _current_span.reset(tok_s)
        _current_trace.reset(tok_t)
        _open_traces.pop(tr.id, None)
        try:
            from . import sink
            sink.submit(tr)
        except Exception:  # noqa: BLE001 - telemetria nunca derruba o fluxo
            pass


# --------------------------------------------------------------------------- #
# Abertura de trace e span
# --------------------------------------------------------------------------- #

@contextmanager
def start_trace(name: str, *, kind: str, user_id: str | None = None,
                method: str = "", path: str = "") -> Iterator[Trace]:
    """Abre um trace raiz. Ao sair, fecha e ENVIA ao sink (import tardio para
    evitar ciclo). Um trace já aberto no contexto NÃO é reiniciado — o bloco vira
    um no-op reutilizando o corrente, então instrumentar um caminho que já roda
    dentro de um trace não cria traces duplicados."""
    existing = _current_trace.get()
    if existing is not None:
        yield existing
        return

    tr = new_trace(name, kind=kind, user_id=user_id, method=method, path=path)
    with activate_trace(tr):
        yield tr


@contextmanager
def span(name: str, *, kind: str = "internal", **attrs: Any) -> Iterator[Span]:
    """Abre um span filho do span/trace corrente. Sem trace ativo, é um no-op
    (devolve um span solto que ninguém grava) — instrumentar código que às vezes
    roda fora de um trace não custa nada nem quebra."""
    tr = _current_trace.get()
    if tr is None:
        # span órfão: mede mas não persiste; mantém a API uniforme
        orphan = Span(id=uuid.uuid4().hex, trace_id="", parent_id=None, name=name,
                      kind=kind, started_wall=time.time(), _t0=_now())
        orphan.set(**attrs)
        try:
            yield orphan
        finally:
            orphan.close()
        return

    parent = _current_span.get()
    sp = Span(id=uuid.uuid4().hex, trace_id=tr.id,
              parent_id=parent.id if parent else None, name=name[:200], kind=kind,
              started_wall=time.time(), _t0=_now())
    sp.set(**attrs)
    tok = _current_span.set(sp)
    err = ""
    try:
        yield sp
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        sp.close(err)
        _current_span.reset(tok)
        tr.add_span(sp)
