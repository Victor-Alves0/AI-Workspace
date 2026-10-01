"""Chegou mensagem nova no WhatsApp? contra o Postgres real: o que entra pela API
oficial fica no histórico, e a IA do chat vê as conversas recentes — com quem ainda
está sem resposta marcado."""
from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from .conftest import migrar, pytestmark  # noqa: F401


def test_mensagens_novas_da_api_oficial(banco, engine, monkeypatch):
    from aiworkspace.integrations import messaging_service as ms
    from aiworkspace.integrations import whatsapp_history as wh
    from aiworkspace.models import User, WhatsAppConnection

    migrar(engine, "head")
    with Session(engine) as s:
        u = User(email=f"w{uuid.uuid4().hex[:6]}@t.local", hashed_password="x")
        s.add(u)
        s.flush()
        conn = WhatsAppConnection(user_id=u.id, provider="official", label="Loja",
                                  auto_reply=False, phone_number_id="1", access_token="t")
        s.add(conn)
        s.commit()
        cid = conn.id
        assert conn.auto_reply is False and conn.compaction is False

    async def _cenario() -> None:
        eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1))
        sm = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(wh, "SessionLocal", sm)
        monkeypatch.setattr(ms, "SessionLocal", sm)
        try:
            agora = int(time.time())
            await wh.record_for(cid, [
                {"jid": "5511", "msg_id": "a1", "text": "oi, tem estoque?", "sender_name": "Ana", "ts": agora - 300},
                {"jid": "5522", "msg_id": "b1", "text": "obrigado", "sender_name": "Beto", "ts": agora - 200},
                {"jid": "5522", "msg_id": "b2", "text": "de nada", "from_me": True, "ts": agora - 100},
                {"jid": "5533", "msg_id": "c1", "text": "antiga", "sender_name": "Caio", "ts": agora - 3 * 86400},
            ])
            novas = await ms.new_messages("whatsapp", str(cid), since_hours=24)
            assert [c["chat"] for c in novas] == ["5522", "5511"]  # mais recente primeiro
            ana = next(c for c in novas if c["chat"] == "5511")
            assert ana["unanswered"] and ana["name"] == "Ana"
            assert ana["messages"][0]["text"] == "oi, tem estoque?"
            assert not next(c for c in novas if c["chat"] == "5522")["unanswered"]

            sem_resposta = await ms.new_messages("whatsapp", str(cid), 24, unanswered_only=True)
            assert [c["chat"] for c in sem_resposta] == ["5511"]
            # janela maior alcança a antiga
            assert len(await ms.new_messages("whatsapp", str(cid), since_hours=24 * 7)) == 3

            # a API oficial agora lê e lista pelo histórico gravado
            lidas = await ms.read_messages("whatsapp", str(cid), "+55 22")
            assert [m["text"] for m in lidas] == ["obrigado", "de nada"]
            conversas = await ms.list_chats("whatsapp", str(cid))
            assert {c["id"] for c in conversas} == {"5511", "5522", "5533"}
        finally:
            await eng.dispose()

    asyncio.run(_cenario())
