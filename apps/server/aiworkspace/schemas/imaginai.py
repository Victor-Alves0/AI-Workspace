"""Contratos HTTP do Imaginai."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class CampaignCreate(BaseModel):
    chat_id: uuid.UUID
    name: str = Field(default="Nome da Campanha", min_length=1, max_length=255)
    system_key: str = Field(default="dnd5e", pattern=r"^[a-z0-9_-]{2,64}$")
    system_version: str = Field(default="5e", max_length=32)
    character_name: str = Field(default="Nome do personagem", min_length=1, max_length=255)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    narration_style: Literal["balanced", "cinematic", "gritty"] | None = None
    difficulty: Literal["story", "balanced", "challenging"] | None = None
    premise: str | None = Field(default=None, max_length=5_000)
    opening_scene: str | None = Field(default=None, max_length=5_000)
    starting_location_name: str | None = Field(default=None, min_length=1, max_length=255)
    starting_location_description: str | None = Field(default=None, max_length=5_000)


class AbilityScoresUpdate(BaseModel):
    strength: int | None = Field(default=None, ge=1, le=30)
    dexterity: int | None = Field(default=None, ge=1, le=30)
    constitution: int | None = Field(default=None, ge=1, le=30)
    intelligence: int | None = Field(default=None, ge=1, le=30)
    wisdom: int | None = Field(default=None, ge=1, le=30)
    charisma: int | None = Field(default=None, ge=1, le=30)


class CharacterUpdate(BaseModel):
    """Campos iniciais da ficha D&D 5e editáveis pelo jogador.

    Recursos de jogo (itens, moedas, espaços de magia e vida durante um turno)
    seguem sendo alterados exclusivamente pelo World Kernel.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    character_class: str | None = Field(default=None, min_length=1, max_length=80)
    level: int | None = Field(default=None, ge=1, le=20)
    ancestry: str | None = Field(default=None, max_length=120)
    background: str | None = Field(default=None, max_length=120)
    alignment: str | None = Field(default=None, max_length=80)
    hp_current: int | None = Field(default=None, ge=0, le=9_999)
    hp_max: int | None = Field(default=None, ge=1, le=9_999)
    armor_class: int | None = Field(default=None, ge=0, le=99)
    speed: int | None = Field(default=None, ge=0, le=999)
    attributes: AbilityScoresUpdate | None = None


class EntityCreate(BaseModel):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,120}$")
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20_000)
    location_id: uuid.UUID | None = None
    owner_entity_id: uuid.UUID | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    private_notes: str | None = Field(default=None, max_length=100_000)


class FactCreate(BaseModel):
    subject_id: uuid.UUID | None = None
    predicate: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{1,95}$")
    object_entity_id: uuid.UUID | None = None
    value: dict[str, Any] = Field(default_factory=dict)
    truth_status: Literal["canonical", "established", "rumor", "proposed", "false"] = (
        "established"
    )
    visibility: Literal["public", "party", "gm"] = "gm"
    locked: bool = False
    valid_from_tick: int | None = Field(default=None, ge=0)
    valid_until_tick: int | None = Field(default=None, ge=0)

    @field_validator("valid_until_tick")
    @classmethod
    def _valid_interval(cls, value: int | None, info):
        start = info.data.get("valid_from_tick")
        if value is not None and start is not None and value < start:
            raise ValueError("valid_until_tick deve ser maior ou igual a valid_from_tick")
        return value


class KnowledgeCreate(BaseModel):
    knower_entity_id: uuid.UUID
    fact_id: uuid.UUID
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    believes_true: bool = True


class ActionRequest(BaseModel):
    actor_id: uuid.UUID
    action_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    target_id: uuid.UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class JournalCreate(BaseModel):
    title: str = Field(default="Sem título", min_length=1, max_length=255)
    content: str = Field(default="", max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    pinned: bool = False

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, tags: list[str]) -> list[str]:
        clean: list[str] = []
        for raw in tags:
            tag = " ".join(raw.split()).strip()[:40]
            if tag and tag.casefold() not in {item.casefold() for item in clean}:
                clean.append(tag)
        return clean


class JournalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=100_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    pinned: bool | None = None

    @field_validator("tags")
    @classmethod
    def _clean_optional_tags(cls, tags: list[str] | None) -> list[str] | None:
        return JournalCreate._clean_tags(tags) if tags is not None else None
