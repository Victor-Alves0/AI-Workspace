"""Histórico do WhatsApp embutido contra o Postgres real: gravação sem duplicar, ordem,
nomes das conversas, mídia e exclusão junto com a conexão."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from .conftest import migrar, pytestmark  # noqa: F401


def test_historico_grava_le_e_some_com_a_conexao(banco, engine, monkeypatch):
    from aiworkspace.integrations import whatsapp_history as wh
    from aiworkspace.models import User, WhatsAppConnection

    migrar(engine, "head")
    instancia = f"wa-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        u = User(email=f"w{uuid.uuid4().hex[:6]}@t.local", hashed_password="x")
        s.add(u)
        s.flush()
        s.add(WhatsAppConnection(user_id=u.id, provider="evolution", instance=instancia))
        s.commit()

    async def _cenario() -> None:
        eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1))
        monkeypatch.setattr(wh, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        wh.forget(instancia)
        try:
            dm, grupo = "5511888@s.whatsapp.net", "123-456@g.us"
            linhas = [
                {"jid": dm, "msg_id": "a1", "text": "e aí", "sender_name": "Beto", "ts": 1_700_000_000},
                {"jid": dm, "msg_id": "a2", "text": "tudo", "from_me": True, "ts": 1_700_000_060},
                {"jid": grupo, "msg_id": "g1", "text": "", "kind": "audio", "media": b"\x01\x02",
                 "ts": 1_700_000_120},
            ]
            assert await wh.record(instancia, linhas) == 3
            assert await wh.record(instancia, linhas[:1]) == 0  # repetida: ignorada

            msgs = await wh.messages(instancia, dm, 10)
            assert [m["text"] for m in msgs] == ["e aí", "tudo"]  # cronológica
            assert msgs[0]["sender_name"] == "Beto" and msgs[1]["from_me"]
            assert [m["msg_id"] for m in await wh.messages(instancia, dm, 1)] == ["a2"]
            assert (await wh.messages(instancia, grupo, 5))[0]["text"] == "[audio]"

            conversas = await wh.chats(instancia)
            assert [c["jid"] for c in conversas] == [grupo, dm]  # mais recente primeiro
            assert conversas[1]["name"] == "Beto" and conversas[0]["is_group"]
            assert await wh.unnamed_groups(instancia) == [grupo]
            await wh.record(instancia, [], {grupo: ("Família", True)})
            assert (await wh.chats(instancia))[0]["name"] == "Família"
            assert await wh.unnamed_groups(instancia) == []

            assert await wh.media(instancia, "g1") == b"\x01\x02"
            assert await wh.media(instancia, "a1") is None
            # instância desconhecida: nada quebra
            assert await wh.record("nao-existe", linhas) == 0
            assert await wh.messages("nao-existe", dm) == []
        finally:
            await eng.dispose()

    asyncio.run(_cenario())

    with engine.begin() as c:
        c.execute(text("DELETE FROM whatsapp_connections WHERE instance = :i"), {"i": instancia})
        restantes = c.execute(text("SELECT (SELECT count(*) FROM whatsapp_messages) + "
                                   "(SELECT count(*) FROM whatsapp_chats)")).scalar()
    assert restantes == 0
