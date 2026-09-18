"""Efeitos sonoros na narração: a IA marca o som, a interface toca.

A IA escreve `[[som: descrição | rótulo]]` no ponto exato em que o som acontece. A
interface troca o marcador por um botão (e, se o usuário quiser, toca sozinho). O som
só é GERADO quando alguém toca — narração que ninguém ouve não gasta crédito — e fica
em cache por descrição: repetir "porta batendo" na campanha é instantâneo e grátis.

Por que marcador e não uma ferramenta: uma chamada de tool por som travaria a
narração a cada efeito (ida e volta ao modelo) e pagaria a geração mesmo sem ninguém
ouvir. O marcador sai junto com o texto, sem custo nenhum para o turno.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import GeneratedImage, SoundEffect

MAX_PROMPT_CHARS = 200

# Instrução ao modelo — em inglês e direta, como as descrições de tools do projeto.
INSTRUCTION = """## Sound effects
You can place a playable sound in your reply with `[[som: <sound description in English> | <short label in the user's language>]]`, e.g. `[[som: heavy wooden door slamming shut | porta batendo]]`.
- Put it inline, at the exact moment the sound happens in the text.
- Describe the SOUND itself, concretely (material, action, intensity): "steel sword clashing against a shield", "goblin snarling", "ceramic plate shattering on stone". Not the story, not dialogue.
- Use 0–3 per reply, only where a sound adds to the moment. Never explain or mention the marker.
- The interface turns it into a play button; sound generation happens only when played."""

# Um pedido por descrição de cada vez: autoplay + clique no mesmo som não podem
# gerar (e cobrar) duas vezes.
_locks: dict[str, asyncio.Lock] = {}


def normalize(prompt: str) -> str:
    """A mesma descrição, escrita de jeitos levemente diferentes, é o mesmo som."""
    texto = re.sub(r"\s+", " ", (prompt or "").strip().lower())
    return texto.strip(" .,;:!?\"'")[:MAX_PROMPT_CHARS]


def cache_key(prompt: str) -> str:
    return hashlib.sha256(normalize(prompt).encode("utf-8")).hexdigest()


async def available(db: AsyncSession, user_id: str | uuid.UUID) -> bool:
    """Só faz sentido pedir marcadores de som se houver quem os gere."""
    from .integrations import elevenlabs_service

    return await elevenlabs_service.get_provider(db, str(user_id)) is not None


async def instruction_if_available(db: AsyncSession, user_id: str | uuid.UUID) -> str | None:
    return INSTRUCTION if await available(db, user_id) else None


class SoundUnavailable(Exception):
    """Sem conexão com a ElevenLabs ligada — não há como gerar o som."""


async def get_or_generate(db: AsyncSession, user_id: uuid.UUID, prompt: str) -> dict[str, Any]:
    """Devolve `{url, cached}` do som; gera na ElevenLabs só na primeira vez."""
    from starlette.concurrency import run_in_threadpool

    from .integrations import elevenlabs_service
    from .providers import image_gen

    texto = normalize(prompt)
    if not texto:
        raise ValueError("Descreva o som.")
    key = cache_key(texto)

    async def _cached() -> dict[str, Any] | None:
        row = await db.scalar(
            select(SoundEffect).where(SoundEffect.user_id == user_id, SoundEffect.key == key)
        )
        return {"url": image_gen.sign_image_url(str(row.media_id)), "cached": True} if row else None

    hit = await _cached()
    if hit:
        return hit

    lock = _locks.setdefault(f"{user_id}:{key}", asyncio.Lock())
    async with lock:
        hit = await _cached()          # outro pedido pode ter gerado enquanto esperávamos
        if hit:
            return hit
        cfg = await elevenlabs_service.get_provider(db, str(user_id))
        if not cfg:
            raise SoundUnavailable(
                "Conecte a ElevenLabs em Integrações para gerar efeitos sonoros."
            )
        audio, mime = await run_in_threadpool(
            elevenlabs_service.sound_effect, cfg["api_key"], texto,
        )
        media = GeneratedImage(
            user_id=user_id, mime=mime, data=audio, prompt=texto, model="elevenlabs-sfx",
        )
        db.add(media)
        await db.flush()
        db.add(SoundEffect(user_id=user_id, key=key, prompt=texto, media_id=media.id))
        await db.commit()
        return {"url": image_gen.sign_image_url(str(media.id)), "cached": False}
