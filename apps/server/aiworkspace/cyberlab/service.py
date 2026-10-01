"""Serviço transacional do caso CyberLab."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CyberLabCase

VALID_MODES = {"blackbox", "autofuzz"}

# Fases por modo. O blackbox é guiado (human-in-the-loop); o autofuzz é AUTÔNOMO:
# a IA escreve o harness, compila com sanitizers, roda o fuzzer no sandbox e tria os
# crashes. Roda sempre sobre o código do projeto Codespace vinculado, dentro do
# sandbox (sem rede) — o isolamento é como fuzzing se faz, não um guard-rail.
PHASES_BY_MODE = {
    "blackbox": ["scoping", "recon", "enum", "analysis", "reporting"],
    "autofuzz": ["scoping", "harness", "build", "fuzz", "triage", "reporting"],
}
# união ordenada e deduplicada → enum ÚNICO do tool (a validação é por modo)
ALL_PHASES = list(dict.fromkeys(p for ps in PHASES_BY_MODE.values() for p in ps))


def phases_for(mode: str) -> list[str]:
    return PHASES_BY_MODE.get(mode, PHASES_BY_MODE["blackbox"])


async def get_case(
    db: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID
) -> CyberLabCase | None:
    return await db.scalar(
        select(CyberLabCase).where(
            CyberLabCase.chat_id == chat_id,
            CyberLabCase.user_id == user_id,
        )
    )


async def create_case(
    db: AsyncSession,
    user_id: uuid.UUID,
    chat_id: uuid.UUID,
    mode: str = "blackbox",
) -> CyberLabCase:
    """Cria o caso do chat. Idempotente: se já existe, devolve o atual (o 1º envio e
    o efeito de ativação do front podem correr, como no Imaginai)."""
    mode = mode if mode in VALID_MODES else "blackbox"
    existing = await get_case(db, user_id, chat_id)
    if existing is not None:
        return existing
    case = CyberLabCase(
        user_id=user_id, chat_id=chat_id, mode=mode, settings={"log": [], "findings": []}
    )
    db.add(case)
    try:
        await db.commit()
    except IntegrityError:
        # corrida: outro caminho criou o caso entre o get e o commit
        await db.rollback()
        return await get_case(db, user_id, chat_id)  # type: ignore[return-value]
    await db.refresh(case)
    return case
