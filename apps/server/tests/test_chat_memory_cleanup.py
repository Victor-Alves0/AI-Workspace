"""Memórias com escopo de chat acompanham o ciclo de vida da conversa."""

import uuid
from types import SimpleNamespace

import pytest

from aiworkspace.chat import routes


@pytest.mark.asyncio
async def test_delete_chat_also_deletes_its_memory_scope(monkeypatch):
    chat_id = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4())
    events: list[object] = []

    class Database:
        async def delete(self, chat):
            events.append(("delete-chat", chat))

        async def commit(self):
            events.append("commit")

    chat = object()

    async def owned(*_args):
        return chat

    async def secret(*_args):
        return "memory-key"

    def delete_scope(key, user_id, *, scope, chat_id):
        events.append(("delete-memory", key, user_id, scope, chat_id))
        return -1

    monkeypatch.setattr(routes, "_get_owned_chat", owned)
    monkeypatch.setattr(routes, "get_secret", secret)
    monkeypatch.setattr(routes.mem0_service, "delete_scope", delete_scope)

    assert await routes.delete_chat(chat_id, user=user, db=Database()) == {"ok": True}
    assert events == [
        ("delete-chat", chat),
        "commit",
        ("delete-memory", "memory-key", str(user.id), "chat", str(chat_id)),
    ]
