"""Analítica com intervalo de datas escolhido pelo usuário (from/to): conta só o que
caiu dentro do intervalo, nas duas visões (geral e por modelo)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from .conftest import migrar, pytestmark  # noqa: F401


async def _cenario(url: str) -> dict:
    from aiworkspace.analytics_routes import model_detail, overview
    from aiworkspace.models import UsageEvent, User

    eng = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1))
    hoje = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
            db.add(user)
            await db.flush()
            for dias, tokens in ((40, 1000), (20, 200), (10, 30), (1, 4)):
                db.add(UsageEvent(user_id=user.id, model="openai/gpt-4o-mini", model_name="mini",
                                  total_tokens=tokens, cost=tokens / 1000,
                                  created_at=hoje - timedelta(days=dias)))
            await db.commit()
            ini = (hoje - timedelta(days=25)).date().isoformat()
            fim = (hoje - timedelta(days=5)).date().isoformat()
            ov = await overview(range_="7d", from_=ini, to=fim, tz_offset=0, db=db, user=user)
            md = await model_detail(key="openai/gpt-4o-mini", range_="7d", from_=ini, to=fim,
                                    tz_offset=0, db=db, user=user)
            pronto = await overview(range_="7d", from_=None, to=None, tz_offset=0, db=db, user=user)
            return {"ov": ov, "md": md, "pronto": pronto, "ini": ini, "fim": fim}
    finally:
        await eng.dispose()


def test_intervalo_conta_so_o_que_caiu_dentro(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_cenario(banco))

    ov, md = r["ov"], r["md"]
    assert ov["range"] == "custom" and ov["from"] == r["ini"] and ov["to"] == r["fim"]
    assert ov["totals"]["tokens"] == 230           # 20 e 10 dias atrás; nem 40 nem 1
    assert md["totals"]["tokens"] == 230
    assert ov["per_day"][0]["date"] == r["ini"] and ov["per_day"][-1]["date"] == r["fim"]
    # sem from/to: janela pronta de 7 dias, até hoje
    assert r["pronto"]["range"] == "7d" and r["pronto"]["totals"]["tokens"] == 4
