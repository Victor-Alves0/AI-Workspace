"""Caixa de correio dos agentes (subagentes) que estão trabalhando AGORA.

O painel de um agente tem uma caixa de texto. Enquanto ele trabalha, o que o usuário
escreve entra no trabalho dele na próxima volta do loop (o mesmo steer do chat
principal, via `TurnSession.steer_drain`), sem recomeçar nada.

Cada agente é identificado como a UI o identifica: o id da chamada `delegate`
(`call_…`), ou `call_…#n` para o n-ésimo membro de uma equipe. O id chega até o
runner por uma contextvar — o dispatcher a liga ao disparar cada agente — e o runner
a zera ao entrar, para os agentes que ESTE agente criar não herdarem a caixa dele.
Chave completa: `<chat>:<id>`. Vale só para o processo (o agente roda nele).
"""

from __future__ import annotations

import contextvars
import threading

current_ref: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent_ref", default=None)
# "pensar mais" pedido na caixa do painel (esforço de raciocínio da resposta do agente)
force_effort: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent_effort", default=None)

_lock = threading.Lock()
_boxes: dict[str, list[str]] = {}


def key(chat_id: str | None, ref: str | None) -> str | None:
    return f"{chat_id}:{ref}" if chat_id and ref else None


def open_box(k: str) -> None:
    with _lock:
        _boxes.setdefault(k, [])


def close_box(k: str) -> None:
    with _lock:
        _boxes.pop(k, None)


def is_open(k: str) -> bool:
    with _lock:
        return k in _boxes


def send(k: str, text: str) -> bool:
    """Entrega ao agente em andamento. False = ele não está trabalhando agora."""
    with _lock:
        box = _boxes.get(k)
        if box is None:
            return False
        box.append(text)
        return True


def drain(k: str) -> list[str]:
    with _lock:
        box = _boxes.get(k)
        if not box:
            return []
        out = list(box)
        box.clear()
        return out
