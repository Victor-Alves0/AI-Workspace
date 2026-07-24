"""Rastreamento fim-a-fim (traces + spans) persistido em Postgres.

Um **trace** é uma operação ponta-a-ponta (uma requisição HTTP, um turno de chat,
um job de automação). Dentro dele, **spans** aninhados marcam cada etapa — uma
query no banco, uma chamada ao provedor de IA, uma ferramenta, o RAG. Cada span
sabe sua duração, quantas leituras/escritas no banco fez e quanto tempo passou
nelas, o tempo de conexão HTTP, e se deu erro.

Princípios:
  - **Nunca derruba nem atrasa a request.** A gravação é assíncrona e em lote; se
    o buffer enche, spans são descartados (com contador), nunca enfileirados a
    ponto de travar. Qualquer exceção no tracing é engolida.
  - **Metadados, não conteúdo.** Por padrão os spans guardam tamanhos, contagens,
    ids e SQL normalizado — nunca o texto de mensagens, prompts ou segredos. Um
    toggle de admin liga a captura de conteúdo pontualmente.
  - **Propagação por contextvar.** O trace/span corrente vive em context vars, que
    o asyncio e o bridge greenlet do SQLAlchemy propagam sozinhos.

API pública: `start_trace`, `span`, `current_trace`, `annotate`, `record_error`,
e os controles do sink (`sink.start`/`stop`).
"""

from .context import (
    Span,
    Trace,
    annotate,
    current_span,
    current_trace,
    record_error,
    set_trace_user,
    span,
    start_trace,
    trace_id_of_current,
)
from . import sink

__all__ = [
    "Span",
    "Trace",
    "annotate",
    "current_span",
    "current_trace",
    "record_error",
    "set_trace_user",
    "span",
    "start_trace",
    "trace_id_of_current",
    "sink",
]
