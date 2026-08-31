"""Regressões pequenas encontradas na varredura geral do backend."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Response

from aiworkspace.api import mgmt_routes
from aiworkspace.auth import routes as auth_routes
from aiworkspace.schemas.auth import RegisterIn


@pytest.mark.asyncio
async def test_delete_end_user_memory_uses_requested_scope(monkeypatch):
    seen: dict[str, object] = {}

    async def immediate(fn, *args):
        return fn(*args)

    def list_memories(_key, _owner, **kwargs):
        seen["filter"] = kwargs
        return [{"id": "memory-1"}]

    def delete_memory(_key, memory_id, owner_id):
        seen["deleted"] = (memory_id, owner_id)
        return True

    monkeypatch.setattr(mgmt_routes, "run_in_threadpool", immediate)

    async def mem_key(*_args):
        return "key"

    monkeypatch.setattr(mgmt_routes, "_mem_key", mem_key)
    monkeypatch.setattr(mgmt_routes.mem0_service, "list_memories", list_memories)
    monkeypatch.setattr(mgmt_routes.mem0_service, "delete_memory", delete_memory)

    ctx = SimpleNamespace(
        key=SimpleNamespace(id="key-1", memory={"mode": "end_user"}),
        user=SimpleNamespace(id="owner-1"),
        db=object(),
    )
    result = await mgmt_routes.delete_memory("memory-1", user="alice", ctx=ctx)

    assert result == {"ok": True}
    assert seen["filter"]["chat_id"] == "api:key-1:alice"
    assert seen["deleted"] == ("memory-1", "owner-1")


@pytest.mark.asyncio
async def test_registration_locks_before_deciding_first_admin(monkeypatch):
    events: list[str] = []

    class FakeDb:
        async def execute(self, *_args, **_kwargs):
            events.append("lock")

        async def scalar(self, _stmt):
            events.append("scalar")
            # consulta do e-mail, depois count(*)
            return None if events.count("scalar") == 1 else 0

        def add(self, user):
            self.user = user

        async def commit(self):
            events.append("commit")

        async def refresh(self, _user):
            pass

    async def allow(_db):
        events.append("signups")
        return True

    monkeypatch.setattr(auth_routes, "signups_allowed", allow)
    monkeypatch.setattr(auth_routes, "check_login_rate", lambda *_: None)
    monkeypatch.setattr(auth_routes, "hash_password", lambda _pw: "hash")
    monkeypatch.setattr(auth_routes, "_set_auth_cookies", lambda *_: None)

    db = FakeDb()
    user = await auth_routes.register(
        RegisterIn(email="admin@example.com", password="password123"),
        request=SimpleNamespace(), response=Response(), db=db,
    )

    assert events[:4] == ["lock", "signups", "scalar", "scalar"]
    assert user.role == "admin"
    assert user.status == "active"
