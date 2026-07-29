"""Acesso a segredos por usuário (cifrados com Fernet)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .crypto import decrypt, encrypt
from .models import UserSecret

logger = logging.getLogger(__name__)

OPENROUTER_KEY = "openrouter_api_key"
TAVILY_KEY = "tavily_api_key"
BRAVE_KEY = "brave_api_key"
VOICE_KEY = "voice_api_key"
FINNHUB_KEY = "finnhub_api_key"
ALPHAVANTAGE_KEY = "alphavantage_api_key"
# Provedor de geração de imagens compatível com OpenAI (DALL-E, SD local). O modelo
# de imagem via OpenRouter usa a própria chave do OpenRouter — esta é só p/ o provedor externo.
IMAGEGEN_KEY = "imagegen_api_key"
# Config da wake word (JSON cifrado): {picovoice_key, ppn_url, vosk_model_url}.
# A AccessKey do Picovoice é chave de cliente (vai ao navegador), mas fica cifrada
# em repouso — sai do profile (texto claro) e não vaza no dump de backup.
WAKE_CONFIG_KEY = "wake_config"


async def set_secret(db: AsyncSession, user_id: uuid.UUID, name: str, value: str) -> None:
    existing = await db.scalar(
        select(UserSecret).where(UserSecret.user_id == user_id, UserSecret.name == name)
    )
    if existing:
        existing.ciphertext = encrypt(value)
    else:
        db.add(UserSecret(user_id=user_id, name=name, ciphertext=encrypt(value)))
    await db.commit()


async def get_secret(db: AsyncSession, user_id: uuid.UUID, name: str) -> str | None:
    row = await db.scalar(
        select(UserSecret).where(UserSecret.user_id == user_id, UserSecret.name == name)
    )
    if row is None:
        return None
    try:
        return decrypt(row.ciphertext)
    except Exception:  # noqa: BLE001 — ex.: APP_SECRET trocado invalida o ciphertext
        logger.warning(
            "Segredo '%s' ilegível (APP_SECRET mudou?); tratando como não configurado",
            name,
        )
        return None


async def has_secret(db: AsyncSession, user_id: uuid.UUID, name: str) -> bool:
    row = await db.scalar(
        select(UserSecret.id).where(
            UserSecret.user_id == user_id, UserSecret.name == name
        )
    )
    return row is not None
