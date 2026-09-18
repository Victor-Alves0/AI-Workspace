"""Autorização de mídia para conversas compartilhadas.

Um token de compartilhamento não é, por si só, suficiente: ele precisa apontar para
o ``public_id`` que ainda está ativo no chat do mesmo dono. Revogar, rotacionar ou
expirar um compartilhamento corta imediatamente o acesso às mídias já publicadas.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chat


def _not_expired(chat: Chat) -> bool:
    expires_at = chat.public_expires_at
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


async def shared_chat_allows(
    db: AsyncSession,
    *,
    chat_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    public_id: str | None,
) -> bool:
    """Confere que a mídia pertence ao chat publicamente compartilhado atual."""
    if chat_id is None or not public_id:
        return False
    chat = await db.get(Chat, chat_id)
    return bool(
        chat is not None
        and chat.user_id == owner_id
        and chat.public_id == public_id
        and _not_expired(chat)
    )


async def public_chat_allows_owner(
    db: AsyncSession, *, owner_id: uuid.UUID, public_id: str | None
) -> bool:
    """Valida um documento que não possui vínculo direto a um chat.

    O URL só é emitido enquanto se serializa um chat compartilhado, mas ainda
    verificamos que aquele compartilhamento pertence ao dono do documento e segue
    ativo antes de servir os bytes.
    """
    if not public_id:
        return False
    from sqlalchemy import select

    chat = await db.scalar(
        select(Chat).where(Chat.public_id == public_id, Chat.user_id == owner_id)
    )
    return bool(chat is not None and _not_expired(chat))
