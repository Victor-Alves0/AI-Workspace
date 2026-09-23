"""//roll: a rolagem vira mensagem que a IA lê no próximo turno e que a interface
desenha como cartão (usage.kind == "dice"), não como bolha do usuário."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from .conftest import migrar, pytestmark  # noqa: F401


async def _cenario(url: str) -> dict:
    from aiworkspace.chat.messages_routes import RollIn, roll_dice
    from aiworkspace.models import Chat, Message, User

    eng = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
            db.add(user)
            await db.flush()
            chat = Chat(user_id=user.id, title="t", model="openai/gpt-4o-mini")
            db.add(chat)
            await db.commit()
            r = await roll_dice(chat.id, RollIn(expression="2d6+1", label="dano"), user=user, db=db)
            # a MESMA seleção que o envio usa para montar o histórico do turno
            rows = list(await db.scalars(
                select(Message).where(
                    Message.chat_id == chat.id,
                    Message.role.in_(("user", "assistant")),
                    Message.compacted.is_(False),
                ).order_by(Message.created_at)
            ))
            return {"r": r, "hist": [(m.role, m.content) for m in rows if m.content],
                    "usage": rows[0].usage}
    finally:
        await eng.dispose()


def test_rolagem_entra_no_contexto_e_guarda_os_dados(banco, engine):
    migrar(engine, "head")
    out = asyncio.run(_cenario(banco))

    [(role, texto)] = out["hist"]
    assert role == "user" and texto.startswith("🎲 Rolagem de dados do usuário — 2d6+1 (dano):")
    assert texto.endswith(f"= {out['r']['result']['total']}")
    assert "**" not in texto
    u = out["usage"]
    assert u["kind"] == "dice" and u["label"] == "dano" and u["expression"] == "2d6+1"
    assert 3 <= u["total"] <= 13 and len(u["terms"][0]["rolls"]) == 2
