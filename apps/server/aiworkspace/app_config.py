"""Configurações globais geridas pelo admin (persistidas em app_settings)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AppSetting, User

ALLOW_SIGNUPS = "allow_signups"


async def get_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    row = await db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        return default
    return row.value.get("v", default) if isinstance(row.value, dict) else default


async def set_setting(db: AsyncSession, key: str, value: Any) -> None:
    row = await db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        db.add(AppSetting(key=key, value={"v": value}))
    else:
        row.value = {"v": value}
    await db.commit()


async def user_count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User)) or 0


async def signups_allowed(db: AsyncSession) -> bool:
    """Cadastro liberado se NÃO houver usuários (bootstrap do admin) ou se o
    admin tiver habilitado explicitamente."""
    if await user_count(db) == 0:
        return True
    return bool(await get_setting(db, ALLOW_SIGNUPS, False))
