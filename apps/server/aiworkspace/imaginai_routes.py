"""API interna do mini app Imaginai."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .imaginai import service, system_definition
from .models import ImaginaiEvent, User
from .schemas.imaginai import (
    ActionRequest,
    CampaignCreate,
    CampaignUpdate,
    CharacterUpdate,
    EntityCreate,
    FactCreate,
    JournalCreate,
    JournalUpdate,
    KnowledgeCreate,
)

router = APIRouter(prefix="/mini-apps/imaginai", tags=["mini-apps", "imaginai"])
ApprovedUser = Annotated[User, Depends(require_approved)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _raise_domain_error(exc: Exception) -> None:
    if isinstance(exc, service.WorldNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, service.WorldConflictError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    raise exc


@router.get("/systems/{system_key}")
async def get_system_definition(system_key: str, _user: ApprovedUser):
    try:
        return system_definition(system_key)
    except ValueError as exc:
        _raise_domain_error(exc)


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreate,
    user: ApprovedUser,
    db: DbSession,
):
    """Cria, de forma idempotente, o mundo vinculado a um chat."""
    try:
        return await service.create_campaign(db, user.id, body)
    except Exception as exc:  # noqa: BLE001 - convertido por tipo abaixo
        _raise_domain_error(exc)


@router.get("/campaigns/by-chat/{chat_id}")
async def get_campaign_by_chat(
    chat_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.campaign_for_chat(db, user.id, chat_id)
        return await service.public_snapshot(db, campaign)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.public_snapshot(db, campaign)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignUpdate,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.update_campaign(db, campaign, body)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


class MapPositions(BaseModel):
    # {id do local: {"x": 0–100, "y": 0–100} | null}
    positions: dict[str, dict[str, float] | None] = Field(default_factory=dict)


@router.patch("/campaigns/{campaign_id}/map/positions")
async def save_map_positions(
    campaign_id: uuid.UUID,
    body: MapPositions,
    user: ApprovedUser,
    db: DbSession,
):
    """Posições que o jogador arrumou arrastando os locais no mapa."""
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id, lock=True)
        return await service.save_map_positions(db, campaign, body.positions)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        _raise_domain_error(exc)


class SpellsUpdate(BaseModel):
    spells: list[dict[str, Any]] = Field(default_factory=list, max_length=60)


@router.put("/campaigns/{campaign_id}/character/spells")
async def replace_spells(
    campaign_id: uuid.UUID,
    body: SpellsUpdate,
    user: ApprovedUser,
    db: DbSession,
):
    """O jogador edita o grimório no painel (lista inteira)."""
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id, lock=True)
        return await service.write_spells(db, campaign, body.spells, replace=True)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        _raise_domain_error(exc)


@router.patch("/campaigns/{campaign_id}/character")
async def update_character(
    campaign_id: uuid.UUID,
    body: CharacterUpdate,
    user: ApprovedUser,
    db: DbSession,
):
    """Edita a base da ficha; ações durante a aventura continuam no kernel."""
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id, lock=True)
        return await service.update_player_character(db, campaign, body)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        _raise_domain_error(exc)


@router.post("/campaigns/{campaign_id}/entities", status_code=status.HTTP_201_CREATED)
async def create_entity(
    campaign_id: uuid.UUID,
    body: EntityCreate,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.create_entity(db, campaign, body)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}/world/entities")
async def get_world_builder_entities(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    """Visão de autoria — não é o Codex filtrado que chega ao personagem."""
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.world_builder_entities(db, campaign)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.post("/campaigns/{campaign_id}/facts", status_code=status.HTTP_201_CREATED)
async def create_fact(
    campaign_id: uuid.UUID,
    body: FactCreate,
    user: ApprovedUser,
    db: DbSession,
):
    """Lore gerada deve nascer como `proposed`; só promoção posterior a torna verdade."""
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.create_fact(db, campaign, body)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.post("/campaigns/{campaign_id}/knowledge", status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    campaign_id: uuid.UUID,
    body: KnowledgeCreate,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.create_knowledge(db, campaign, body)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.post("/campaigns/{campaign_id}/actions/validate")
async def validate_action(
    campaign_id: uuid.UUID,
    body: ActionRequest,
    user: ApprovedUser,
    db: DbSession,
):
    """Prévia sem efeitos: ideal para o interpretador converter fala em intenção."""
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        decision, _, _ = await service.evaluate_action(db, campaign, body)
        return decision.as_dict()
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.post("/campaigns/{campaign_id}/actions/resolve")
async def resolve_action(
    campaign_id: uuid.UUID,
    body: ActionRequest,
    user: ApprovedUser,
    db: DbSession,
):
    """Valida novamente sob lock e só então grava mutações + evento atômico."""
    try:
        return await service.resolve_action(db, user.id, campaign_id, body)
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}/events")
async def list_events(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
    limit: int = 100,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)
    limit = max(1, min(limit, 500))
    events = list(
        await db.scalars(
            select(ImaginaiEvent)
            .where(ImaginaiEvent.campaign_id == campaign.id)
            .order_by(ImaginaiEvent.sequence.desc())
            .limit(limit)
        )
    )
    return {
        "events": [
            {
                "id": str(event.id),
                "sequence": event.sequence,
                "world_tick": event.world_tick,
                "event_type": event.event_type,
                "actor_id": str(event.actor_id) if event.actor_id else None,
                "target_id": str(event.target_id) if event.target_id else None,
                "location_id": str(event.location_id) if event.location_id else None,
                "payload": event.payload,
                "visibility": event.visibility,
                "created_at": event.created_at,
            }
            for event in events
        ]
    }


@router.get("/campaigns/{campaign_id}/journal")
async def list_journal(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
    search: str = "",
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return {"entries": await service.list_journal(db, campaign, search)}
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.post("/campaigns/{campaign_id}/journal", status_code=status.HTTP_201_CREATED)
async def create_journal_entry(
    campaign_id: uuid.UUID,
    body: JournalCreate,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.create_journal_entry(db, campaign, body)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.patch("/campaigns/{campaign_id}/journal/{entry_id}")
async def update_journal_entry(
    campaign_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: JournalUpdate,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.update_journal_entry(db, campaign, entry_id, body)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.delete(
    "/campaigns/{campaign_id}/journal/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_journal_entry(
    campaign_id: uuid.UUID,
    entry_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        await service.delete_journal_entry(db, campaign, entry_id)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}/inventory")
async def get_inventory(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.inventory_snapshot(db, campaign)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}/spells")
async def get_spells(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.spells_snapshot(db, campaign)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}/map")
async def get_map(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.map_snapshot(db, campaign)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)


@router.get("/campaigns/{campaign_id}/codex")
async def search_codex(
    campaign_id: uuid.UUID,
    user: ApprovedUser,
    db: DbSession,
    search: str = "",
    kind: str = "",
):
    try:
        campaign = await service.owned_campaign(db, user.id, campaign_id)
        return await service.codex_search(db, campaign, search, kind)
    except Exception as exc:  # noqa: BLE001
        _raise_domain_error(exc)
