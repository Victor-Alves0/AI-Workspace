"""Imagens de entidades do mundo (NPC, local, item, facção, o próprio personagem).

A IA NÃO gera imagem sozinha: quando o jogador pede, ela usa a ferramenta de imagem
que tiver (gerador próprio, Civitai, Higgsfield). Todas guardam o arquivo em
`generated_images` e devolvem `/images/<id>?t=<token>`. Aqui a imagem é VINCULADA à
entidade (`state.image.id`) — e passa a aparecer no Codex, no mapa e na ficha, e pode
ser mostrada de novo no chat sem gerar outra.

Só vale imagem do próprio usuário: o id é conferido contra o dono no banco (uma URL
de outra pessoa colada no chat não vira retrato de ninguém).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GeneratedImage, ImaginaiCampaign, ImaginaiEntity
from ..providers.image_gen import sign_image_url

_IMAGE_ID = re.compile(r"/images/([0-9a-fA-F-]{36})")


class ImageLinkError(ValueError):
    """Pedido inválido — a mensagem volta ao narrador."""


def image_id_from_url(url: str) -> uuid.UUID | None:
    match = _IMAGE_ID.search(url or "")
    if not match:
        return None
    try:
        return uuid.UUID(match.group(1))
    except ValueError:
        return None


def image_url(entity: ImaginaiEntity | None) -> str | None:
    """URL assinada da imagem da entidade, ou None."""
    if entity is None:
        return None
    image = (entity.state or {}).get("image")
    if not isinstance(image, dict) or not image.get("id"):
        return None
    return sign_image_url(str(image["id"]))


async def find_entity(db: AsyncSession, campaign: ImaginaiCampaign, reference: str) -> ImaginaiEntity | None:
    """Por id, chave ou nome (sem diferenciar maiúsculas). "player"/"personagem" = o PJ."""
    ref = (reference or "").strip()
    if not ref:
        return None
    if ref.casefold() in {"player", "personagem", "jogador", "eu"}:
        ref = "player"
    conditions = [ImaginaiEntity.key == ref, func.lower(ImaginaiEntity.name) == ref.casefold()]
    try:
        conditions.append(ImaginaiEntity.id == uuid.UUID(ref))
    except ValueError:
        pass
    return await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.active.is_(True),
            or_(*conditions),
        ).limit(1)
    )


async def link_image(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, target: str, url: str,
) -> dict[str, Any]:
    entity = await find_entity(db, campaign, target)
    if entity is None:
        raise ImageLinkError(f"Entidade não encontrada: {target!r}. Use o nome exato.")
    image_id = image_id_from_url(url)
    if image_id is None:
        raise ImageLinkError(
            "image_url precisa ser a URL devolvida pela ferramenta de imagem (/images/<id>?t=...)."
        )
    owner = await db.scalar(select(GeneratedImage.user_id).where(GeneratedImage.id == image_id))
    if owner != user_id:
        raise ImageLinkError("Imagem não encontrada entre as geradas por este usuário.")
    state = dict(entity.state or {})
    state["image"] = {"id": str(image_id)}
    entity.state = state
    await db.commit()
    return {"entity": entity.name, "kind": entity.kind, "image_url": image_url(entity)}


async def show_image(db: AsyncSession, campaign: ImaginaiCampaign, target: str) -> dict[str, Any]:
    entity = await find_entity(db, campaign, target)
    if entity is None:
        raise ImageLinkError(f"Entidade não encontrada: {target!r}.")
    url = image_url(entity)
    if url is None:
        return {"entity": entity.name, "image_url": None,
                "note": "Sem imagem salva. Só gere uma se o jogador pedir."}
    return {"entity": entity.name, "image_url": url, "markdown": f"![{entity.name}]({url})"}


async def images_in_campaign(db: AsyncSession, campaign: ImaginaiCampaign) -> list[str]:
    """Nomes das entidades que já têm imagem (vai no contexto do turno: o narrador sabe
    que pode MOSTRAR sem gerar de novo)."""
    rows = await db.scalars(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.active.is_(True),
            ImaginaiEntity.state["image"].isnot(None),
        )
    )
    return [e.name for e in rows if image_url(e)]
