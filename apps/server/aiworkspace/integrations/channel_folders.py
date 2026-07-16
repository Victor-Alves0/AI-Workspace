"""Organização de pastas dos canais (WhatsApp/Telegram/Discord).

O layout antigo criava <Canal>/<conta>/Chats — um nível a mais sem função (a
pasta da conta não tinha mais nada). O layout novo põe as conversas DIRETO na
pasta da conta; esta migração suave roda quando `_ensure_folder` encontra o
layout antigo: move os chats (e subpastas do usuário) p/ o pai e apaga a "Chats".
"""

from __future__ import annotations

from sqlalchemy import update

from ..models import Chat, Folder


async def collapse_chats_folder(db, folder: Folder):
    """Se `folder` é a subpasta legada "Chats", colapsa no pai e devolve o id do
    pai; None = não é o layout antigo (nada a fazer)."""
    if folder.name != "Chats" or folder.parent_id is None:
        return None
    parent = await db.get(Folder, folder.parent_id)
    if parent is None:
        return None
    await db.execute(update(Chat).where(Chat.folder_id == folder.id).values(folder_id=parent.id))
    await db.execute(update(Folder).where(Folder.parent_id == folder.id).values(parent_id=parent.id))
    await db.delete(folder)
    await db.flush()
    return parent.id
