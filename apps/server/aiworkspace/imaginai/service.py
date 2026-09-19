"""Serviço transacional do World Kernel do Imaginai."""

from __future__ import annotations

import copy
import json
import math
import re
import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Chat,
    ImaginaiActionAttempt,
    ImaginaiCampaign,
    ImaginaiEntity,
    ImaginaiEvent,
    ImaginaiFact,
    ImaginaiJournalEntry,
    ImaginaiKnowledge,
)
from ..schemas.imaginai import (
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
from . import encounters
from . import images as entity_images
from .rules import ActionDecision, ActionIntent, EntitySnapshot, _blocked, ruleset_for
from .systems import system_definition


class WorldNotFoundError(LookupError):
    pass


class WorldConflictError(ValueError):
    pass


async def turn_has_attempt(
    db: AsyncSession,
    user_id: uuid.UUID,
    chat_id: uuid.UUID,
    turn_key: str,
) -> bool:
    attempt_id = await db.scalar(
        select(ImaginaiActionAttempt.id)
        .join(
            ImaginaiCampaign,
            ImaginaiCampaign.id == ImaginaiActionAttempt.campaign_id,
        )
        .where(
            ImaginaiCampaign.chat_id == chat_id,
            ImaginaiCampaign.user_id == user_id,
            ImaginaiActionAttempt.input["_turn_key"].astext == turn_key,
        )
        .limit(1)
    )
    return attempt_id is not None


def _campaign_out(campaign: ImaginaiCampaign) -> dict[str, Any]:
    return {
        "id": str(campaign.id),
        "chat_id": str(campaign.chat_id),
        "name": campaign.name,
        "system_key": campaign.system_key,
        "system_version": campaign.system_version,
        "status": campaign.status,
        "world_tick": campaign.world_tick,
        "setup_stage": campaign.setup_stage,
        "settings": campaign.settings or {},
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
    }


def _entity_out(entity: ImaginaiEntity) -> dict[str, Any]:
    # private_notes nunca atravessa este contrato.
    return {
        "id": str(entity.id),
        "campaign_id": str(entity.campaign_id),
        "kind": entity.kind,
        "key": entity.key,
        "name": entity.name,
        "description": entity.description,
        "location_id": str(entity.location_id) if entity.location_id else None,
        "owner_entity_id": str(entity.owner_entity_id) if entity.owner_entity_id else None,
        "state": entity.state or {},
        "active": entity.active,
        "image_url": entity_images.image_url(entity),
    }


def _snapshot(entity: ImaginaiEntity) -> EntitySnapshot:
    return EntitySnapshot(
        id=str(entity.id),
        kind=entity.kind,
        key=entity.key,
        name=entity.name,
        location_id=str(entity.location_id) if entity.location_id else None,
        owner_entity_id=str(entity.owner_entity_id) if entity.owner_entity_id else None,
        state=copy.deepcopy(entity.state or {}),
        active=entity.active,
    )


async def owned_campaign(
    db: AsyncSession, user_id: uuid.UUID, campaign_id: uuid.UUID, *, lock: bool = False
) -> ImaginaiCampaign:
    query = select(ImaginaiCampaign).where(
        ImaginaiCampaign.id == campaign_id,
        ImaginaiCampaign.user_id == user_id,
    )
    if lock:
        query = query.with_for_update()
    campaign = await db.scalar(query)
    if campaign is None:
        raise WorldNotFoundError("Campanha não encontrada")
    return campaign


async def campaign_for_chat(
    db: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID
) -> ImaginaiCampaign:
    campaign = await db.scalar(
        select(ImaginaiCampaign).where(
            ImaginaiCampaign.chat_id == chat_id,
            ImaginaiCampaign.user_id == user_id,
        )
    )
    if campaign is None:
        raise WorldNotFoundError("Campanha não encontrada")
    return campaign


async def create_campaign(
    db: AsyncSession, user_id: uuid.UUID, body: CampaignCreate
) -> dict[str, Any]:
    chat = await db.scalar(select(Chat).where(Chat.id == body.chat_id, Chat.user_id == user_id))
    if chat is None:
        raise WorldNotFoundError("Chat não encontrado")
    existing = await db.scalar(
        select(ImaginaiCampaign).where(ImaginaiCampaign.chat_id == body.chat_id)
    )
    if existing is not None:
        if existing.user_id != user_id:
            raise WorldNotFoundError("Campanha não encontrada")
        return await public_snapshot(db, existing)

    # Falha antes de gravar se o plugin não existe.
    ruleset_for(body.system_key)
    chat.mini_app = "imaginai"
    campaign = ImaginaiCampaign(
        user_id=user_id,
        chat_id=body.chat_id,
        name=body.name.strip(),
        system_key=body.system_key,
        system_version=body.system_version,
        settings={
            # Consistência é rígida; criatividade não. O adjudicador deve deixar
            # ações plausíveis seguirem e usar testes/consequências quando houver
            # incerteza, sem transformar o kernel numa sequência de recusas.
            "world_consistency": "strict",
            "action_creativity": "permissive",
            "checks": "only_when_uncertain_and_consequential",
            "failure_mode": "fail_forward",
            "narration_style": "balanced",
            "difficulty": "balanced",
        },
    )
    db.add(campaign)
    try:
        await db.flush()
    except IntegrityError:
        # O clique no Mini App e o primeiro envio podem chegar juntos. O índice
        # único de chat decide o vencedor; o perdedor recupera a mesma campanha.
        await db.rollback()
        existing = await db.scalar(
            select(ImaginaiCampaign).where(
                ImaginaiCampaign.chat_id == body.chat_id,
                ImaginaiCampaign.user_id == user_id,
            )
        )
        if existing is None:
            raise
        return await public_snapshot(db, existing)

    starting_location = ImaginaiEntity(
        campaign_id=campaign.id,
        user_id=user_id,
        kind="location",
        key="starting-location",
        name="Local inicial",
        description="",
        state={"discovered": True, "map": {"x": 50, "y": 50}},
    )
    db.add(starting_location)
    await db.flush()

    character = ImaginaiEntity(
        campaign_id=campaign.id,
        user_id=user_id,
        kind="character",
        key="player",
        name=body.character_name.strip(),
        location_id=starting_location.id,
        state={
            "dnd5e": {
                "class": "Classe",
                "level": 1,
                "hp": {"current": 10, "max": 10},
                "armor_class": 10,
                "spells": [],
                "spell_slots": {},
                "discovered_entity_ids": [],
                "attributes": {
                    key: 10
                    for key in (
                        "strength",
                        "dexterity",
                        "constitution",
                        "intelligence",
                        "wisdom",
                        "charisma",
                    )
                },
                "skills": {},
                "currencies": {"cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0},
                "proficiency_bonus": 2,
                "initiative": 0,
                "speed": 30,
                "passive_perception": 10,
            }
        },
    )
    db.add(character)
    await db.commit()
    await db.refresh(campaign)
    return await public_snapshot(db, campaign)


async def public_snapshot(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    character = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        )
    )
    location = None
    if character is not None and character.location_id:
        location = await db.get(ImaginaiEntity, character.location_id)
        if location is not None and location.campaign_id != campaign.id:
            location = None
    return {
        "campaign": _campaign_out(campaign),
        "character": _entity_out(character) if character else None,
        "location": _entity_out(location) if location else None,
        # combate como o jogador o vê (vida do inimigo como estado, não número)
        "encounter": (
            await encounters.summary(db, campaign, character) if character else None
        ),
    }


async def update_campaign(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: CampaignUpdate,
) -> dict[str, Any]:
    if body.name is not None:
        campaign.name = body.name.strip()
    settings = copy.deepcopy(campaign.settings or {})
    if body.narration_style is not None:
        settings["narration_style"] = body.narration_style
    if body.difficulty is not None:
        settings["difficulty"] = body.difficulty
    for key in ("premise", "opening_scene"):
        value = getattr(body, key)
        if value is not None:
            settings[key] = value.strip()
    campaign.settings = settings
    if body.starting_location_name is not None or body.starting_location_description is not None:
        location = await db.scalar(
            select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.kind == "location",
                ImaginaiEntity.key == "starting-location",
            ).with_for_update()
        )
        if location is None:
            raise WorldNotFoundError("Local inicial não encontrado")
        if body.starting_location_name is not None:
            location.name = body.starting_location_name.strip()
        if body.starting_location_description is not None:
            location.description = body.starting_location_description.strip()
    await db.commit()
    await db.refresh(campaign)
    return await public_snapshot(db, campaign)


_ABILITY_KEYS = (
    "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
)


def _score(value: Any, fallback: int = 10) -> int:
    if isinstance(value, dict):
        value = value.get("score", fallback)
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _proficiency_bonus(level: int) -> int:
    """Progressão oficial 5e: +2 nos níveis 1–4 até +6 nos 17–20."""
    return 2 + max(0, min(19, level - 1)) // 4


def _merge_character_setup(state: dict[str, Any], body: CharacterUpdate) -> dict[str, Any]:
    """Atualiza somente o setup da ficha e deriva valores que não são livres."""
    result = copy.deepcopy(state if isinstance(state, dict) else {})
    dnd = result.get("dnd5e")
    dnd = copy.deepcopy(dnd) if isinstance(dnd, dict) else {}

    if body.character_class is not None:
        dnd["class"] = body.character_class.strip()
    for key in ("ancestry", "background", "alignment", "backstory"):
        value = getattr(body, key)
        if value is not None:
            dnd[key] = value.strip()
    if body.level is not None:
        dnd["level"] = body.level
    level = max(1, min(20, _score(dnd.get("level"), 1)))
    dnd["level"] = level

    attributes = dnd.get("attributes")
    attributes = copy.deepcopy(attributes) if isinstance(attributes, dict) else {}
    if body.attributes is not None:
        for key, value in body.attributes.model_dump(exclude_none=True).items():
            attributes[key] = value
        # ficha montada à mão: a sessão zero não cobra os passos da criação guiada
        dnd["sheet_source"] = "manual"
    if body.save_proficiencies is not None:
        marcadas = set(body.save_proficiencies)
        dnd["saving_throws"] = {key: {"proficient": key in marcadas} for key in _ABILITY_KEYS}
    if body.skill_proficiencies is not None:
        pericias = system_definition("dnd5e")["sheet"]["skills"]
        especialista = set(body.skill_expertise or [])
        dnd["skills"] = {
            key: {"proficient": True, **({"proficiency": 2} if key in especialista else {})}
            for key in body.skill_proficiencies if key in pericias
        }
    for key in _ABILITY_KEYS:
        attributes[key] = max(1, min(30, _score(attributes.get(key), 10)))
    dnd["attributes"] = attributes

    hp = dnd.get("hp")
    hp = copy.deepcopy(hp) if isinstance(hp, dict) else {}
    if body.hp_max is not None:
        hp["max"] = body.hp_max
    maximum_hp = max(1, _score(hp.get("max"), 10))
    if body.hp_current is not None:
        hp["current"] = body.hp_current
    hp["max"] = maximum_hp
    hp["current"] = max(0, min(maximum_hp, _score(hp.get("current"), maximum_hp)))
    dnd["hp"] = hp

    if body.armor_class is not None:
        dnd["armor_class"] = body.armor_class
    if body.speed is not None:
        dnd["speed"] = body.speed
    dnd["proficiency_bonus"] = _proficiency_bonus(level)
    dexterity_modifier = (_score(attributes["dexterity"]) - 10) // 2
    wisdom_modifier = (_score(attributes["wisdom"]) - 10) // 2
    skills = dnd.get("skills") if isinstance(dnd.get("skills"), dict) else {}
    perception = skills.get("perception", 0)
    perception_rank = (
        _score(perception.get("proficiency"), 1 if perception.get("proficient") else 0)
        if isinstance(perception, dict) else _score(perception, 0)
    )
    dnd["initiative"] = dexterity_modifier
    dnd["passive_perception"] = 10 + wisdom_modifier + dnd["proficiency_bonus"] * min(2, max(0, perception_rank))
    result["dnd5e"] = dnd
    return result


def _spell_attack_modifier(dnd: dict[str, Any]) -> int | None:
    value = dnd.get("spell_attack_modifier")
    if value is None and isinstance(dnd.get("spellcasting"), dict):
        value = dnd["spellcasting"].get("attack_modifier")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def write_spells(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    incoming: list[Any],
    *,
    remove: list[Any] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Grava magias da ficha (jogador no painel ou narrador no jogo). `replace` troca a
    lista inteira (editor); senão adiciona/atualiza por nome e remove as indicadas."""
    from . import spells as spellbook

    character = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        ).with_for_update()
    )
    if character is None:
        raise WorldNotFoundError("Personagem não encontrado")
    state = copy.deepcopy(character.state or {})
    dnd = state.get("dnd5e") if isinstance(state.get("dnd5e"), dict) else {}
    atual = [] if replace else dnd.get("spells", [])
    dnd["spells"] = spellbook.merge(atual, incoming, _spell_attack_modifier(dnd), remove=remove)
    state["dnd5e"] = dnd
    character.state = state
    await db.commit()
    return await spells_snapshot(db, campaign)


async def update_player_character(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: CharacterUpdate,
) -> dict[str, Any]:
    character = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        ).with_for_update()
    )
    if character is None:
        raise WorldNotFoundError("Personagem não encontrado")
    if body.name is not None:
        character.name = body.name.strip()
    character.state = _merge_character_setup(character.state or {}, body)
    await db.commit()
    await db.refresh(campaign)
    return await public_snapshot(db, campaign)


async def _owned_entity(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    entity_id: uuid.UUID,
    *,
    lock: bool = False,
) -> ImaginaiEntity:
    query = select(ImaginaiEntity).where(
        ImaginaiEntity.id == entity_id,
        ImaginaiEntity.campaign_id == campaign.id,
        ImaginaiEntity.user_id == campaign.user_id,
    )
    if lock:
        query = query.with_for_update()
    entity = await db.scalar(query)
    if entity is None:
        raise WorldNotFoundError("Entidade não encontrada")
    return entity


async def create_entity(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: EntityCreate,
) -> dict[str, Any]:
    location_id = None
    owner_id = None
    if body.location_id:
        location = await _owned_entity(db, campaign, body.location_id)
        if location.kind != "location":
            raise WorldConflictError("location_id não referencia uma localização")
        location_id = location.id
    if body.owner_entity_id:
        owner_id = (await _owned_entity(db, campaign, body.owner_entity_id)).id
    duplicate = await db.scalar(
        select(ImaginaiEntity.id).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.key == body.key,
        )
    )
    if duplicate:
        raise WorldConflictError("Já existe uma entidade com essa chave")
    entity = ImaginaiEntity(
        campaign_id=campaign.id,
        user_id=campaign.user_id,
        kind=body.kind,
        key=body.key,
        name=body.name,
        description=body.description,
        location_id=location_id,
        owner_entity_id=owner_id,
        state=body.state,
        private_notes=body.private_notes,
    )
    db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return _entity_out(entity)


async def world_builder_entities(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
) -> dict[str, Any]:
    """Catálogo do proprietário para montar o mundo, separado do Codex do jogador."""
    entities = list(await db.scalars(
        select(ImaginaiEntity)
        .where(ImaginaiEntity.campaign_id == campaign.id)
        .order_by(ImaginaiEntity.kind, ImaginaiEntity.name)
    ))
    return {
        "entities": [
            {
                **_entity_out(entity),
                "private_notes": entity.private_notes or "",
            }
            for entity in entities
        ]
    }


async def create_fact(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: FactCreate,
) -> dict[str, Any]:
    if body.subject_id:
        await _owned_entity(db, campaign, body.subject_id)
    if body.object_entity_id:
        await _owned_entity(db, campaign, body.object_entity_id)
    fact = ImaginaiFact(
        campaign_id=campaign.id,
        user_id=campaign.user_id,
        subject_id=body.subject_id,
        predicate=body.predicate,
        object_entity_id=body.object_entity_id,
        value=body.value,
        truth_status=body.truth_status,
        visibility=body.visibility,
        locked=body.locked,
        valid_from_tick=body.valid_from_tick,
        valid_until_tick=body.valid_until_tick,
    )
    db.add(fact)
    await db.commit()
    await db.refresh(fact)
    return {
        "id": str(fact.id),
        "predicate": fact.predicate,
        "truth_status": fact.truth_status,
        "visibility": fact.visibility,
        "locked": fact.locked,
    }


async def create_knowledge(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: KnowledgeCreate,
) -> dict[str, Any]:
    knower = await _owned_entity(db, campaign, body.knower_entity_id)
    fact = await db.scalar(
        select(ImaginaiFact).where(
            ImaginaiFact.id == body.fact_id,
            ImaginaiFact.campaign_id == campaign.id,
        )
    )
    if fact is None:
        raise WorldNotFoundError("Fato não encontrado")
    existing = await db.scalar(
        select(ImaginaiKnowledge).where(
            ImaginaiKnowledge.knower_entity_id == knower.id,
            ImaginaiKnowledge.fact_id == fact.id,
        )
    )
    if existing is None:
        existing = ImaginaiKnowledge(
            campaign_id=campaign.id,
            user_id=campaign.user_id,
            knower_entity_id=knower.id,
            fact_id=fact.id,
            learned_at_tick=campaign.world_tick,
        )
        db.add(existing)
    existing.confidence = body.confidence
    existing.believes_true = body.believes_true
    await db.commit()
    await db.refresh(existing)
    return {
        "id": str(existing.id),
        "knower_entity_id": str(existing.knower_entity_id),
        "fact_id": str(existing.fact_id),
        "confidence": existing.confidence,
        "believes_true": existing.believes_true,
    }


def _journal_out(entry: ImaginaiJournalEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "campaign_id": str(entry.campaign_id),
        "title": entry.title,
        "content": entry.content,
        "tags": entry.tags or [],
        "pinned": entry.pinned,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


async def list_journal(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    search: str = "",
) -> list[dict[str, Any]]:
    query = select(ImaginaiJournalEntry).where(
        ImaginaiJournalEntry.campaign_id == campaign.id,
        ImaginaiJournalEntry.user_id == campaign.user_id,
    )
    term = search.strip()
    if term:
        pattern = f"%{term[:100]}%"
        query = query.where(
            ImaginaiJournalEntry.title.ilike(pattern) | ImaginaiJournalEntry.content.ilike(pattern)
        )
    query = query.order_by(
        ImaginaiJournalEntry.pinned.desc(),
        ImaginaiJournalEntry.updated_at.desc(),
    ).limit(500)
    return [_journal_out(entry) for entry in await db.scalars(query)]


async def create_journal_entry(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: JournalCreate,
) -> dict[str, Any]:
    entry = ImaginaiJournalEntry(
        campaign_id=campaign.id,
        user_id=campaign.user_id,
        title=body.title.strip(),
        content=body.content,
        tags=body.tags,
        pinned=body.pinned,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return _journal_out(entry)


async def _owned_journal_entry(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    entry_id: uuid.UUID,
) -> ImaginaiJournalEntry:
    entry = await db.scalar(
        select(ImaginaiJournalEntry).where(
            ImaginaiJournalEntry.id == entry_id,
            ImaginaiJournalEntry.campaign_id == campaign.id,
            ImaginaiJournalEntry.user_id == campaign.user_id,
        )
    )
    if entry is None:
        raise WorldNotFoundError("Anotação não encontrada")
    return entry


async def update_journal_entry(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    entry_id: uuid.UUID,
    body: JournalUpdate,
) -> dict[str, Any]:
    entry = await _owned_journal_entry(db, campaign, entry_id)
    if body.title is not None:
        entry.title = body.title.strip()
    if body.content is not None:
        entry.content = body.content
    if body.tags is not None:
        entry.tags = body.tags
    if body.pinned is not None:
        entry.pinned = body.pinned
    await db.commit()
    await db.refresh(entry)
    return _journal_out(entry)


async def delete_journal_entry(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    entry_id: uuid.UUID,
) -> None:
    entry = await _owned_journal_entry(db, campaign, entry_id)
    await db.delete(entry)
    await db.commit()


async def inventory_snapshot(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
) -> dict[str, Any]:
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        )
    )
    if player is None:
        raise WorldNotFoundError("Personagem não encontrado")
    items = list(
        await db.scalars(
            select(ImaginaiEntity)
            .where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.kind == "item",
                ImaginaiEntity.owner_entity_id == player.id,
                ImaginaiEntity.active.is_(True),
            )
            .order_by(ImaginaiEntity.name)
        )
    )
    output: list[dict[str, Any]] = []
    item_weight = 0.0

    def safe_number(value: Any, fallback: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback
        return number if math.isfinite(number) else fallback

    for item in items:
        state = item.state if isinstance(item.state, dict) else {}
        inv = state.get("inventory") if isinstance(state.get("inventory"), dict) else state
        quantity = max(1, int(safe_number(inv.get("quantity", 1), 1)))
        weight = max(0.0, safe_number(inv.get("weight", 0), 0))
        item_weight += quantity * weight
        output.append(
            {
                "id": str(item.id),
                "name": item.name,
                "description": item.description,
                "quantity": quantity,
                "weight": weight,
                "equipped": bool(inv.get("equipped", False)),
                "slot": inv.get("slot"),
                "container": inv.get("container"),
                "charges": inv.get("charges"),
            }
        )
    dnd = (player.state or {}).get("dnd5e", player.state or {})
    raw_currencies = dnd.get("currencies", {}) if isinstance(dnd, dict) else {}
    currencies = raw_currencies if isinstance(raw_currencies, dict) else {}
    inventory_rules = system_definition(campaign.system_key)["inventory"]
    campaign_inventory = (campaign.settings or {}).get("inventory", {})
    campaign_inventory = campaign_inventory if isinstance(campaign_inventory, dict) else {}
    weight_enabled = bool(
        inventory_rules["weight"]["supported"]
        and campaign_inventory.get("weight_enabled", inventory_rules["weight"]["default_enabled"])
    )
    currency_weight_enabled = bool(
        weight_enabled
        and inventory_rules["currency_weight"]["supported"]
        and campaign_inventory.get(
            "currency_weight_enabled",
            inventory_rules["currency_weight"]["default_enabled"],
        )
    )
    currency_weight = 0.0
    normalized_currencies: dict[str, int | float] = {}
    for currency in inventory_rules["currencies"]:
        amount = max(0.0, safe_number(currencies.get(currency["key"], 0), 0))
        normalized_currencies[currency["key"]] = int(amount) if amount.is_integer() else amount
        if currency_weight_enabled:
            currency_weight += amount * float(currency["weight"])
    total_weight = item_weight + currency_weight if weight_enabled else 0.0
    return {
        "items": output,
        "currencies": normalized_currencies,
        "total_weight": round(total_weight, 3),
        "weight": {
            "enabled": weight_enabled,
            "currency_enabled": currency_weight_enabled,
            "items": round(item_weight, 3),
            "currencies": round(currency_weight, 3),
            "total": round(total_weight, 3),
            "unit": inventory_rules["weight"]["unit"],
        },
    }


def _player_dnd_state(player: ImaginaiEntity) -> dict[str, Any]:
    state = player.state if isinstance(player.state, dict) else {}
    dnd = state.get("dnd5e")
    return dnd if isinstance(dnd, dict) else state


def _known_entity_ids(player: ImaginaiEntity) -> set[str]:
    raw = _player_dnd_state(player).get("discovered_entity_ids", [])
    if not isinstance(raw, list):
        return set()
    return {
        str(value)
        for value in raw
        if value
    }


def _visible_to_player(player: ImaginaiEntity, entity: ImaginaiEntity) -> bool:
    state = entity.state if isinstance(entity.state, dict) else {}
    discovery = str(state.get("discovery", "")).casefold()
    return bool(
        entity.id == player.id
        or entity.id == player.location_id
        or state.get("discovered")
        or str(entity.id) in _known_entity_ids(player)
        or discovery in {"aware", "known"}
    )


def _spells_from_state(dnd: dict[str, Any]) -> list[dict[str, Any]]:
    raw = dnd.get("spells", [])
    records: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        records = [
            {"key": str(key), **(value if isinstance(value, dict) else {"name": str(key)})}
            for key, value in raw.items()
        ]
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, str):
                records.append({"key": value, "name": value, "level": 0})
            elif isinstance(value, dict):
                records.append(value)
    spells: list[dict[str, Any]] = []
    for record in records[:100]:
        name = str(record.get("name") or record.get("key") or "Magia")[:255]
        key = str(record.get("key") or name.casefold().replace(" ", "-"))[:120]
        level = max(0, min(9, _score(record.get("level"), 0)))
        spells.append({
            "key": key,
            "name": name,
            "level": level,
            "school": str(record.get("school") or "")[:80],
            "prepared": record.get("prepared", True) is not False,
            "known": record.get("known", True) is not False,
            "ritual": bool(record.get("ritual", False)),
            "concentration": bool(record.get("concentration", False)),
            "casting_time": str(record.get("casting_time") or record.get("castingTime") or "")[:80],
            "range": str(record.get("range") or "")[:80],
            "duration": str(record.get("duration") or "")[:80],
            "components": record.get("components", []),
            "description": str(record.get("description") or "")[:4_000],
            # mecânica editável pelo jogador (o `effect` é remontado ao salvar)
            "damage": str(record.get("damage") or (record.get("effect") or {}).get("damage") or "")[:20],
            "damage_type": str(record.get("damage_type") or "")[:40],
            "attack": bool(record.get("attack", (record.get("effect") or {}).get("roll_kind") == "spell_attack")),
            "save": str(record.get("save") or "")[:20],
            "healing": _score(record.get("healing") or (record.get("effect") or {}).get("healing"), 0),
        })
    return sorted(spells, key=lambda spell: (spell["level"], spell["name"].casefold()))


async def spells_snapshot(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        )
    )
    if player is None:
        raise WorldNotFoundError("Personagem não encontrado")
    dnd = _player_dnd_state(player)
    raw_slots = dnd.get("spell_slots", {})
    slots: dict[str, dict[str, int]] = {}
    if isinstance(raw_slots, dict):
        for level in range(1, 10):
            raw = raw_slots.get(str(level), raw_slots.get(level))
            if not isinstance(raw, dict):
                continue
            maximum = max(0, _score(raw.get("max"), 0))
            slots[str(level)] = {
                "current": max(0, min(maximum, _score(raw.get("current"), maximum))),
                "max": maximum,
            }
    return {
        "spells": _spells_from_state(dnd),
        "slots": slots,
        "spellcasting_ability": str(dnd.get("spellcasting_ability") or "") or None,
        "attack_modifier": _score(dnd.get("spell_attack_modifier"), 0),
        "save_dc": _score(dnd.get("spell_save_dc"), 0),
    }


def _map_coordinate(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number)) if math.isfinite(number) else None


async def map_snapshot(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    """Mapa do jogador: nunca retorna local/rota antes de serem descobertos."""
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        )
    )
    if player is None:
        raise WorldNotFoundError("Personagem não encontrado")
    locations = list(await db.scalars(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "location",
            ImaginaiEntity.active.is_(True),
        ).order_by(ImaginaiEntity.name)
    ))
    visible = [location for location in locations if _visible_to_player(player, location)]
    by_reference = {
        value: location
        for location in visible
        for value in (str(location.id), location.key, location.name.casefold())
    }
    nodes: list[dict[str, Any]] = []
    for index, location in enumerate(visible):
        state = location.state if isinstance(location.state, dict) else {}
        map_state = state.get("map") if isinstance(state.get("map"), dict) else state
        nodes.append({
            "id": str(location.id),
            "name": location.name,
            "description": location.description[:600],
            "current": location.id == player.location_id,
            "x": _map_coordinate(map_state.get("x")),
            "y": _map_coordinate(map_state.get("y")),
            "index": index,
            "image_url": entity_images.image_url(location),
        })
    routes: list[dict[str, str]] = []
    seen_routes: set[tuple[str, str]] = set()
    for location in visible:
        state = location.state if isinstance(location.state, dict) else {}
        map_state = state.get("map") if isinstance(state.get("map"), dict) else state
        raw_links = map_state.get("connections", map_state.get("routes", map_state.get("exits", [])))
        if isinstance(raw_links, dict):
            raw_links = [{"to": key, **(value if isinstance(value, dict) else {})} for key, value in raw_links.items()]
        if not isinstance(raw_links, list):
            continue
        for raw in raw_links[:30]:
            link = raw if isinstance(raw, dict) else {"to": raw}
            reference = str(link.get("to") or link.get("location") or link.get("target") or "").strip()
            target = by_reference.get(reference) or by_reference.get(reference.casefold())
            if target is None or target.id == location.id:
                continue
            pair = tuple(sorted((str(location.id), str(target.id))))
            if pair in seen_routes:
                continue
            seen_routes.add(pair)
            routes.append({
                "from": str(location.id),
                "to": str(target.id),
                "label": str(link.get("label") or link.get("travel") or "")[:120],
            })
    return {"locations": nodes, "routes": routes}


async def codex_search(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    search: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """Busca somente no conhecimento do jogador; ausência nunca revela existência."""
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        )
    )
    if player is None:
        raise WorldNotFoundError("Personagem não encontrado")
    dnd = (player.state or {}).get("dnd5e", player.state or {})
    discovered = {
        str(value)
        for value in (dnd.get("discovered_entity_ids", []) if isinstance(dnd, dict) else [])
    }
    entities = list(
        await db.scalars(
            select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.active.is_(True),
            )
        )
    )
    wanted = search.strip().casefold()
    kind_filter = kind.strip().casefold()
    results: list[dict[str, Any]] = []
    visible_entity_names: dict[uuid.UUID, str] = {}
    for entity in entities:
        state = entity.state if isinstance(entity.state, dict) else {}
        discovery = str(state.get("discovery", "")).casefold()
        known = (
            entity.id == player.id
            or entity.id == player.location_id
            or bool(state.get("discovered"))
            or str(entity.id) in discovered
            or discovery == "known"
        )
        aware = discovery == "aware"
        if not known and not aware:
            continue
        visible_entity_names[entity.id] = entity.name
        if entity.id == player.id:
            continue                      # o próprio personagem vive na Ficha, não no Codex
        # "npc" no filtro inclui criaturas: para o jogador, os dois são "gente do mundo"
        kinds = {"npc", "creature"} if kind_filter == "npc" else {kind_filter}
        if kind_filter and kind_filter != "all" and entity.kind not in kinds:
            continue
        if wanted and wanted not in entity.name.casefold():
            continue
        codex = state.get("codex") if isinstance(state.get("codex"), dict) else {}
        results.append(
            {
                "id": str(entity.id),
                "result_type": "entity",
                "kind": entity.kind,
                "name": entity.name,
                "knowledge": "known" if known else "aware",
                "description": (codex.get("summary") or entity.description) if known else None,
                "fields": codex.get("fields", {}) if known else {},
                # imagem só do que o personagem conhece (a de um oculto revelaria a aparência)
                "image_url": entity_images.image_url(entity) if known else None,
                "redacted": [] if known else ["description", "details"],
            }
        )

    if not kind_filter or kind_filter in {"all", "lore"}:
        known_facts = list(
            await db.scalars(
                select(ImaginaiKnowledge).where(
                    ImaginaiKnowledge.campaign_id == campaign.id,
                    ImaginaiKnowledge.knower_entity_id == player.id,
                    ImaginaiKnowledge.believes_true.is_(True),
                )
            )
        )
        for knowledge in known_facts:
            fact = await db.get(ImaginaiFact, knowledge.fact_id)
            if fact is None:
                continue
            searchable = f"{fact.predicate} {json.dumps(fact.value, ensure_ascii=False)}"
            if wanted and wanted not in searchable.casefold():
                continue
            results.append(
                {
                    "id": str(fact.id),
                    "result_type": "lore",
                    "kind": "lore",
                    "name": fact.predicate.replace("_", " ").title(),
                    # Um fato conhecido não deve vazar o nome canônico de uma
                    # entidade que o personagem ainda nem sabe que existe.
                    "subject": visible_entity_names.get(fact.subject_id),
                    "knowledge": "rumor" if knowledge.confidence < 0.75 else "known",
                    "confidence": knowledge.confidence,
                    "description": fact.value,
                    "fields": {},
                    "redacted": [],
                }
            )
    return {"results": results[:100], "total": min(len(results), 100)}


async def evaluate_action(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    body: ActionRequest,
    *,
    lock: bool = False,
) -> tuple[ActionDecision, ImaginaiEntity, ImaginaiEntity | None]:
    actor = await _owned_entity(db, campaign, body.actor_id, lock=lock)
    target = None
    if body.target_id:
        try:
            target = await _owned_entity(db, campaign, body.target_id, lock=lock)
        except WorldNotFoundError:
            # Não revela se o UUID pertence a outro mundo/usuário.
            target = None
    intent = ActionIntent(
        action_type=body.action_type,
        actor=_snapshot(actor),
        target=_snapshot(target) if target else None,
        parameters=body.parameters,
    )
    return ruleset_for(campaign.system_key).validate(intent), actor, target


def _set_dnd_state(entity: ImaginaiEntity, dnd_state: dict[str, Any]) -> None:
    state = copy.deepcopy(entity.state or {})
    if isinstance(state.get("dnd5e"), dict):
        state["dnd5e"] = dnd_state
    else:
        state = dnd_state
    entity.state = state


def _set_inventory_state(entity: ImaginaiEntity, inventory: dict[str, Any]) -> None:
    state = copy.deepcopy(entity.state or {})
    if isinstance(state.get("inventory"), dict):
        state["inventory"] = inventory
    else:
        state = inventory
    entity.state = state


def _apply_mutation(mutation: dict[str, Any], entities: dict[str, ImaginaiEntity]) -> None:
    entity = entities.get(str(mutation.get("entity_id", "")))
    if entity is None:
        raise WorldConflictError("Mutação referencia uma entidade fora da ação")
    op = mutation.get("op")
    if op == "set_owner":
        owner = mutation.get("owner_entity_id")
        entity.owner_entity_id = uuid.UUID(owner) if owner else None
        return
    if op == "set_location":
        location = mutation.get("location_id")
        entity.location_id = uuid.UUID(location) if location else None
        return
    if op == "decrement_spell_slot":
        outer = copy.deepcopy(entity.state or {})
        dnd = copy.deepcopy(outer.get("dnd5e", outer))
        slots = copy.deepcopy(dnd.get("spell_slots", {}))
        level = str(int(mutation["level"]))
        slot = copy.deepcopy(slots.get(level, {}))
        current = int(slot.get("current", 0) or 0)
        amount = int(mutation.get("amount", 1) or 1)
        if current < amount:
            raise WorldConflictError("O recurso mudou antes da ação ser confirmada")
        slot["current"] = current - amount
        slots[level] = slot
        dnd["spell_slots"] = slots
        _set_dnd_state(entity, dnd)
        return
    if op == "set_item_equipped":
        outer = copy.deepcopy(entity.state or {})
        inventory = copy.deepcopy(outer.get("inventory", outer))
        inventory["equipped"] = bool(mutation.get("equipped"))
        if mutation.get("slot"):
            inventory["slot"] = str(mutation["slot"])
        _set_inventory_state(entity, inventory)
        return
    if op == "adjust_item_quantity":
        outer = copy.deepcopy(entity.state or {})
        inventory = copy.deepcopy(outer.get("inventory", outer))
        current = max(0, int(inventory.get("quantity", 1) or 0))
        updated = max(0, current + int(mutation.get("amount", 0) or 0))
        inventory["quantity"] = updated
        _set_inventory_state(entity, inventory)
        if updated == 0:
            entity.active = False
        return
    if op == "adjust_currency":
        outer = copy.deepcopy(entity.state or {})
        dnd = copy.deepcopy(outer.get("dnd5e", outer))
        currencies = copy.deepcopy(dnd.get("currencies", {}))
        currency = str(mutation.get("currency") or "")
        if currency not in {"cp", "sp", "ep", "gp", "pp"}:
            raise WorldConflictError("Moeda inválida na mutação")
        current = int(currencies.get(currency, 0) or 0)
        updated = current + int(mutation.get("amount", 0) or 0)
        if updated < 0:
            raise WorldConflictError("O saldo mudou antes da ação ser confirmada")
        currencies[currency] = updated
        dnd["currencies"] = currencies
        _set_dnd_state(entity, dnd)
        return
    if op == "adjust_hp":
        outer = copy.deepcopy(entity.state or {})
        dnd = copy.deepcopy(outer.get("dnd5e", outer))
        hp = copy.deepcopy(dnd.get("hp", {}))
        if not isinstance(hp, dict) or "max" not in hp:
            raise WorldConflictError("A entidade não possui pontos de vida autoritativos")
        maximum = max(0, int(hp.get("max", 0) or 0))
        current = max(0, min(int(hp.get("current", maximum) or 0), maximum))
        hp["current"] = max(
            0,
            min(maximum, current + int(mutation.get("amount", 0) or 0)),
        )
        dnd["hp"] = hp
        _set_dnd_state(entity, dnd)
        return
    if op == "restore_long_rest":
        outer = copy.deepcopy(entity.state or {})
        dnd = copy.deepcopy(outer.get("dnd5e", outer))
        hp = copy.deepcopy(dnd.get("hp", {}))
        if isinstance(hp, dict) and "max" in hp:
            hp["current"] = max(0, int(hp.get("max", 0) or 0))
            dnd["hp"] = hp
        slots = copy.deepcopy(dnd.get("spell_slots", {}))
        if isinstance(slots, dict):
            for level, raw_slot in list(slots.items()):
                if isinstance(raw_slot, dict) and "max" in raw_slot:
                    slot = copy.deepcopy(raw_slot)
                    slot["current"] = max(0, int(slot.get("max", 0) or 0))
                    slots[level] = slot
            dnd["spell_slots"] = slots
        _set_dnd_state(entity, dnd)
        return
    raise WorldConflictError(f"Mutação não suportada: {op}")


async def _mutation_entities(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    actor: ImaginaiEntity,
    target: ImaginaiEntity | None,
    mutations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, ImaginaiEntity]:
    entities = {str(actor.id): actor}
    if target is not None:
        entities[str(target.id)] = target
    for mutation in mutations:
        raw_id = str(mutation.get("entity_id") or "")
        if not raw_id or raw_id in entities:
            continue
        try:
            entity_id = uuid.UUID(raw_id)
        except ValueError as exc:
            raise WorldConflictError("Mutação contém uma entidade inválida") from exc
        entity = await _owned_entity(db, campaign, entity_id, lock=True)
        entities[str(entity.id)] = entity
    return entities


async def _unequip_slot_conflicts(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    owner: ImaginaiEntity,
    equipped_item: ImaginaiEntity,
    slot: str,
) -> None:
    """Mantém um único item por espaço, sob o mesmo lock da campanha."""
    items = list(
        await db.scalars(
            select(ImaginaiEntity)
            .where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.user_id == campaign.user_id,
                ImaginaiEntity.kind == "item",
                ImaginaiEntity.owner_entity_id == owner.id,
                ImaginaiEntity.active.is_(True),
                ImaginaiEntity.id != equipped_item.id,
            )
            .with_for_update()
        )
    )
    for item in items:
        outer = item.state if isinstance(item.state, dict) else {}
        inventory = outer.get("inventory", outer)
        if not isinstance(inventory, dict):
            continue
        if inventory.get("equipped") and str(inventory.get("slot") or "") == slot:
            _apply_mutation(
                {"op": "set_item_equipped", "entity_id": str(item.id), "equipped": False},
                {str(item.id): item},
            )


_DAMAGE_DICE_RE = re.compile(
    r"^\s*(?:(\d{1,2})d(\d{1,3})|(\d+))\s*([+-]\s*\d+)?\s*$", re.IGNORECASE
)


def _roll_damage(expression: str, *, critical: bool = False) -> tuple[int, list[int], int]:
    """Rola uma expressão pequena e segura (ex.: 1d8+3) sem avaliar código."""
    match = _DAMAGE_DICE_RE.fullmatch(expression or "")
    if match is None:
        raise WorldConflictError("O dano do ataque não possui um formato válido")
    count_raw, sides_raw, flat_raw, modifier_raw = match.groups()
    modifier = int((modifier_raw or "0").replace(" ", ""))
    if flat_raw is not None:
        flat = max(0, min(int(flat_raw), 1000))
        return max(0, flat + modifier), [], modifier
    count = max(1, min(int(count_raw or 1), 20))
    sides = max(2, min(int(sides_raw or 6), 100))
    if critical:
        count *= 2
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    return max(0, sum(rolls) + modifier), rolls, modifier


async def resolve_action(
    db: AsyncSession,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID,
    body: ActionRequest,
    *,
    turn_key: str | None = None,
    turn_step: int | None = None,
) -> dict[str, Any]:
    # Lock da campanha serializa sequence e alterações concorrentes na mesma mesa.
    campaign = await owned_campaign(db, user_id, campaign_id, lock=True)
    if turn_key and turn_step is not None:
        existing = await db.scalar(
            select(ImaginaiActionAttempt)
            .where(
                ImaginaiActionAttempt.campaign_id == campaign.id,
                ImaginaiActionAttempt.user_id == user_id,
                ImaginaiActionAttempt.input["_turn_key"].astext == turn_key,
                ImaginaiActionAttempt.input["_turn_step"].astext == str(turn_step),
            )
            .with_for_update()
        )
        if existing is not None:
            return {
                "attempt_id": str(existing.id),
                "event_id": str(existing.event_id) if existing.event_id else None,
                **(existing.decision or {}),
                "status": existing.status,
                "replayed": True,
            }
    decision, actor, target = await evaluate_action(db, campaign, body, lock=True)
    if encounters.active(campaign) and body.action_type == "rest":
        decision = _blocked("in_combat", "Não é possível descansar com inimigos lutando.")
    attempt_input = copy.deepcopy(body.parameters)
    if turn_key:
        attempt_input["_turn_key"] = turn_key
    if turn_step is not None:
        attempt_input["_turn_step"] = str(turn_step)
    attempt = ImaginaiActionAttempt(
        campaign_id=campaign.id,
        user_id=user_id,
        actor_id=actor.id,
        target_id=target.id if target else None,
        action_type=body.action_type,
        input=attempt_input,
        status=decision.status,
        reason_code=decision.reason_code,
        reason=decision.reason,
        decision=decision.as_dict(),
    )
    db.add(attempt)

    event = None
    combate = None
    if decision.status == "allowed":
        entities = await _mutation_entities(db, campaign, actor, target, decision.mutations)
        if body.action_type == "equip_item" and target is not None:
            slot = str(decision.public_payload.get("slot") or "")
            if slot:
                await _unequip_slot_conflicts(db, campaign, actor, target, slot)
        for mutation in decision.mutations:
            _apply_mutation(mutation, entities)
        if decision.consumes_turn:
            campaign.world_tick += max(1, int(decision.tick_cost or 1))
        event = ImaginaiEvent(
            campaign_id=campaign.id,
            user_id=user_id,
            sequence=campaign.next_event_sequence,
            world_tick=campaign.world_tick,
            event_type=decision.event_type or body.action_type,
            actor_id=actor.id,
            target_id=target.id if target else None,
            location_id=actor.location_id,
            payload=decision.public_payload,
            visibility="public",
        )
        campaign.next_event_sequence += 1
        db.add(event)
        await db.flush()
        attempt.event_id = event.id
        attempt.status = "resolved"
        if encounters.is_player(actor):
            if body.action_type == "start_encounter" and not encounters.active(campaign):
                # emboscada: a iniciativa decide quem age primeiro — pode não ser o jogador
                combate = await encounters.start(
                    db, campaign, user_id, actor, target=target, player_already_acted=False,
                )
            elif decision.consumes_turn:
                combate = await encounters.after_player_turn(db, campaign, user_id, actor)

    await db.commit()
    await db.refresh(attempt)
    out = {
        "attempt_id": str(attempt.id),
        "event_id": str(event.id) if event else None,
        **decision.as_dict(),
        "status": attempt.status,
        "replayed": False,
    }
    if combate is not None:
        out["encounter"] = combate
    elif body.action_type == "start_encounter" and decision.status == "allowed":
        out["encounter"] = None
        out["note"] = "Não há inimigos hostis de pé neste local: não houve combate."
    return out


async def _owned_attempt(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    attempt_id: uuid.UUID,
) -> ImaginaiActionAttempt:
    attempt = await db.scalar(
        select(ImaginaiActionAttempt)
        .where(
            ImaginaiActionAttempt.id == attempt_id,
            ImaginaiActionAttempt.campaign_id == campaign.id,
            ImaginaiActionAttempt.user_id == campaign.user_id,
        )
        .with_for_update()
    )
    if attempt is None:
        raise WorldNotFoundError("Tentativa não encontrada")
    return attempt


async def _replayed_resolution(
    db: AsyncSession, attempt: ImaginaiActionAttempt
) -> dict[str, Any] | None:
    if attempt.status != "resolved" or not attempt.event_id:
        return None
    event = await db.get(ImaginaiEvent, attempt.event_id)
    return {
        "attempt_id": str(attempt.id),
        "event_id": str(attempt.event_id),
        "status": "resolved",
        "event_type": event.event_type if event else None,
        "outcome": (event.payload or {}) if event else {},
        "replayed": True,
    }


def _dnd_check_modifier(
    actor: ImaginaiEntity,
    ability: str,
    skill: str | None,
) -> tuple[int, str]:
    definition = system_definition("dnd5e")
    attributes = definition["sheet"]["attributes"]
    skill_ability = {
        skill_key: attribute["key"] for attribute in attributes for skill_key in attribute["skills"]
    }
    if skill:
        if skill not in skill_ability:
            raise ValueError("Perícia inválida para o teste")
        ability = skill_ability.get(skill, ability)
    valid_abilities = {attribute["key"] for attribute in attributes}
    if ability not in valid_abilities:
        raise ValueError("Atributo inválido para o teste")
    dnd = (actor.state or {}).get("dnd5e", actor.state or {})
    dnd = dnd if isinstance(dnd, dict) else {}
    scores = dnd.get("attributes", {})
    scores = scores if isinstance(scores, dict) else {}
    raw_score = scores.get(ability, 10)
    if isinstance(raw_score, dict):
        raw_score = raw_score.get("score", 10)
    try:
        score = int(raw_score or 10)
    except (TypeError, ValueError):
        score = 10
    modifier = (score - 10) // 2
    if skill:
        skills = dnd.get("skills", {})
        skills = skills if isinstance(skills, dict) else {}
        state = skills.get(skill)
        rank = 1 if state is True else 0
        if isinstance(state, dict):
            try:
                rank = int(state.get("proficiency", 1 if state.get("proficient") else 0) or 0)
            except (TypeError, ValueError):
                rank = 0
            explicit = state.get("value")
            if isinstance(explicit, int):
                return explicit, ability
        try:
            proficiency = int(dnd.get("proficiency_bonus", 2) or 2)
        except (TypeError, ValueError):
            proficiency = 2
        modifier += max(0, min(rank, 2)) * proficiency
    return modifier, ability


async def resolve_check(
    db: AsyncSession,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID,
    attempt_id: uuid.UUID,
    *,
    ability: str,
    skill: str | None,
    dc: int,
    advantage: str = "normal",
) -> dict[str, Any]:
    """Resolve um d20 no servidor e grava o resultado no ledger uma única vez."""
    campaign = await owned_campaign(db, user_id, campaign_id, lock=True)
    attempt = await _owned_attempt(db, campaign, attempt_id)
    replayed = await _replayed_resolution(db, attempt)
    if replayed is not None:
        return replayed
    if attempt.status not in {"requires_check", "needs_adjudication"}:
        raise WorldConflictError("Esta ação não está aguardando um teste")
    actor = await _owned_entity(db, campaign, attempt.actor_id, lock=True)
    decision = attempt.decision or {}
    public = decision.get("public_payload", {})
    public = public if isinstance(public, dict) else {}
    roll_kind = str(public.get("roll_kind") or "")
    is_attack = attempt.action_type == "attack" or roll_kind == "spell_attack"
    if is_attack:
        modifier = int(public.get("attack_modifier", 0) or 0)
        resolved_ability = "attack"
        difficulty = max(1, int(public.get("target_ac", 10) or 10))
    else:
        modifier, resolved_ability = _dnd_check_modifier(actor, ability, skill)
        difficulty = max(5, min(int(dc), 30))
    mode = advantage if advantage in {"normal", "advantage", "disadvantage"} else "normal"
    rolls = [secrets.randbelow(20) + 1]
    if mode != "normal":
        rolls.append(secrets.randbelow(20) + 1)
    die = max(rolls) if mode == "advantage" else min(rolls) if mode == "disadvantage" else rolls[0]
    total = die + modifier
    success = die == 20 or (die != 1 and total >= difficulty)
    tick_cost = max(1, int(decision.get("tick_cost", 1) or 1))
    campaign.world_tick += tick_cost
    payload = {
        "attempt_id": str(attempt.id),
        "action_type": attempt.action_type,
        "ability": resolved_ability,
        "skill": skill,
        "dc": difficulty,
        "rolls": rolls,
        "die": die,
        "modifier": modifier,
        "total": total,
        "success": success,
        "result": "success" if success else "failure",
    }
    event_type = decision.get("event_type") or "ability_check_resolved"
    combate = None
    target = None
    if attempt.target_id:
        target = await _owned_entity(db, campaign, attempt.target_id, lock=True)
    mutations = decision.get("mutations", [])
    mutations = mutations if isinstance(mutations, list) else []
    always_mutations = [
        mutation
        for mutation in mutations
        if isinstance(mutation, dict) and mutation.get("when") == "always"
    ]
    success_mutations = [
        mutation
        for mutation in mutations
        if isinstance(mutation, dict) and mutation.get("when", "success") == "success"
    ]
    applicable_mutations = always_mutations + (success_mutations if success else [])
    if applicable_mutations:
        entities = await _mutation_entities(db, campaign, actor, target, mutations)
        for mutation in applicable_mutations:
            _apply_mutation(mutation, entities)
    if is_attack:
        critical = die == 20
        damage = 0
        damage_rolls: list[int] = []
        damage_modifier = 0
        if success:
            damage, damage_rolls, damage_modifier = _roll_damage(
                str(public.get("damage") or "1"), critical=critical
            )
            if target is None:
                raise WorldConflictError("O alvo do ataque não está mais disponível")
            _apply_mutation(
                {"op": "adjust_hp", "entity_id": str(target.id), "amount": -damage},
                {str(target.id): target},
            )
        payload.update(
            {
                "attack_key": public.get("attack_key"),
                "attack_name": public.get("attack_name"),
                "spell_key": public.get("spell_key"),
                "spell_name": public.get("spell_name"),
                "target_ac": difficulty,
                "critical": critical,
                "damage": damage,
                "damage_rolls": damage_rolls,
                "damage_modifier": damage_modifier,
                "damage_type": public.get("damage_type"),
            }
        )
    event = ImaginaiEvent(
        campaign_id=campaign.id,
        user_id=user_id,
        sequence=campaign.next_event_sequence,
        world_tick=campaign.world_tick,
        event_type=event_type,
        actor_id=actor.id,
        target_id=target.id if target else None,
        location_id=actor.location_id,
        payload=payload,
        visibility="public",
    )
    campaign.next_event_sequence += 1
    db.add(event)
    await db.flush()
    attempt.event_id = event.id
    attempt.status = "resolved"
    if encounters.is_player(actor) and decision.get("consumes_turn", True):
        if encounters.active(campaign):
            combate = await encounters.after_player_turn(db, campaign, user_id, actor)
        elif is_attack and target is not None:
            # o primeiro golpe abre o combate: esse ataque já foi o turno do jogador
            combate = await encounters.start(
                db, campaign, user_id, actor, target=target, player_already_acted=True,
            )
    await db.commit()
    out = {
        "attempt_id": str(attempt.id),
        "event_id": str(event.id),
        "status": "resolved",
        "event_type": event.event_type,
        "outcome": payload,
        "replayed": False,
    }
    if combate is not None:
        out["encounter"] = combate
    return out


async def adjudicate_attempt(
    db: AsyncSession,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID,
    attempt_id: uuid.UUID,
    *,
    outcome: str,
    summary: str,
) -> dict[str, Any]:
    """Confirma consequência narrativa sem permitir mutações arbitrárias do modelo."""
    campaign = await owned_campaign(db, user_id, campaign_id, lock=True)
    attempt = await _owned_attempt(db, campaign, attempt_id)
    replayed = await _replayed_resolution(db, attempt)
    if replayed is not None:
        return replayed
    if attempt.status != "needs_adjudication":
        raise WorldConflictError("Esta ação não está aguardando adjudicação narrativa")
    result = outcome if outcome in {"success", "partial", "failure"} else "partial"
    decision = attempt.decision or {}
    if decision.get("consumes_turn"):
        campaign.world_tick += max(1, int(decision.get("tick_cost", 1) or 1))
    payload = {
        "attempt_id": str(attempt.id),
        "action_type": attempt.action_type,
        "result": result,
        "summary": " ".join(summary.split())[:2000],
    }
    actor = await _owned_entity(db, campaign, attempt.actor_id, lock=True)
    target = None
    if attempt.target_id:
        target = await _owned_entity(db, campaign, attempt.target_id, lock=True)
    mutations = decision.get("mutations", [])
    mutations = mutations if isinstance(mutations, list) else []
    always_mutations = [
        mutation
        for mutation in mutations
        if isinstance(mutation, dict) and mutation.get("when") == "always"
    ]
    success_mutations = [
        mutation
        for mutation in mutations
        if isinstance(mutation, dict) and mutation.get("when", "success") == "success"
    ]
    applicable_mutations = always_mutations + (success_mutations if result == "success" else [])
    if applicable_mutations:
        entities = await _mutation_entities(db, campaign, actor, target, mutations)
        for mutation in applicable_mutations:
            _apply_mutation(mutation, entities)
    event = ImaginaiEvent(
        campaign_id=campaign.id,
        user_id=user_id,
        sequence=campaign.next_event_sequence,
        world_tick=campaign.world_tick,
        event_type=decision.get("event_type") or "narrative_outcome",
        actor_id=actor.id,
        target_id=target.id if target else None,
        location_id=actor.location_id,
        payload=payload,
        visibility="public",
    )
    campaign.next_event_sequence += 1
    db.add(event)
    await db.flush()
    attempt.event_id = event.id
    attempt.status = "resolved"
    combate = None
    if encounters.is_player(actor) and decision.get("consumes_turn") and encounters.active(campaign):
        combate = await encounters.after_player_turn(db, campaign, user_id, actor)
    await db.commit()
    out = {
        "attempt_id": str(attempt.id),
        "event_id": str(event.id),
        "status": "resolved",
        "event_type": event.event_type,
        "outcome": payload,
        "replayed": False,
    }
    if combate is not None:
        out["encounter"] = combate
    return out
