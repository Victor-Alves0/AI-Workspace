"""Gravação do ledger de uso (`usage_events`).

Recebe o registro ponta-a-ponta já montado por `chat.routes._usage_record` e cria
uma linha de `UsageEvent`. Chamado logo após persistir a mensagem do assistente —
no chat (send/regenerate/continue) e nas automações agendadas.
"""

from __future__ import annotations

import uuid

from .models import UsageEvent


def _as_uuid(value) -> uuid.UUID | None:
    if not value:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def usage_event_from_record(user_id, chat_id, message_id, rec: dict) -> UsageEvent | None:
    """Monta um `UsageEvent` a partir do `rec` (_usage_record). Retorna None quando
    não há nada a registrar (sem tokens nem custo) — evita ruído no ledger."""
    total = int(rec.get("total_tokens") or 0)
    cost = float(rec.get("cost") or 0.0)
    prompt = int(rec.get("prompt_tokens") or 0)
    completion = int(rec.get("completion_tokens") or 0)
    if not (total or cost or prompt or completion):
        return None
    return UsageEvent(
        user_id=_as_uuid(user_id),
        chat_id=_as_uuid(chat_id),
        message_id=_as_uuid(message_id),
        model_config_id=_as_uuid(rec.get("model_config_id")),
        model=rec.get("model") or "",
        model_name=rec.get("model_name") or rec.get("model") or "",
        provider=rec.get("provider") or "openrouter",
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        reasoning_tokens=int(rec.get("reasoning_tokens") or 0),
        cached_tokens=int(rec.get("cached_tokens") or 0),
        cost=cost,
        tools_breakdown={
            str(k): int(v or 0)
            for k, v in (rec.get("tools_breakdown") or {}).items()
        },
    )
