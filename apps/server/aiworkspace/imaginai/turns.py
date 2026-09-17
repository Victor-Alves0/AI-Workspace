"""Ponte autoritativa entre o loop de chat e o World Kernel do Imaginai.

O modelo recebe uma única ferramenta. Ele interpreta a linguagem natural, mas
nunca escreve diretamente no estado: consultas, rolagens e commits passam por
serviços determinísticos e transacionais deste módulo.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat.orchestrator import NativeToolOpts
from ..db import SessionLocal
from ..models import (
    ImaginaiCampaign,
    ImaginaiEntity,
    ImaginaiEvent,
    ImaginaiFact,
    ImaginaiKnowledge,
)
from ..schemas.imaginai import ActionRequest
from . import service

_WORLD_TOOL = {
    "type": "function",
    "function": {
        "name": "imaginai_world",
        "description": (
            "Consulta e altera, com validação autoritativa, o mundo RPG deste chat. "
            "Use context para atualizar a cena; roleplay antes de interpretar um NPC; "
            "resolve antes de narrar qualquer ação ou consequência; roll quando a "
            "resolução exigir teste; adjudicate para concluir ações criativas sem teste."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["context", "roleplay", "resolve", "roll", "adjudicate"],
                },
                "step": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Ordem estável da ação neste turno; obrigatório em resolve.",
                },
                "action_type": {
                    "type": "string",
                    "description": (
                        "Intenção normalizada, por exemplo take_item, interact, cast_spell, "
                        "examine, move, attack, persuade ou hide."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": "ID ou nome exato de uma entidade já apresentada pela ferramenta.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Parâmetros factuais da intenção; para magia use {spell: nome}.",
                },
                "attempt_id": {
                    "type": "string",
                    "description": "ID retornado por resolve; obrigatório em roll/adjudicate.",
                },
                "ability": {
                    "type": "string",
                    "enum": [
                        "strength",
                        "dexterity",
                        "constitution",
                        "intelligence",
                        "wisdom",
                        "charisma",
                    ],
                },
                "skill": {"type": "string"},
                "dc": {"type": "integer", "minimum": 5, "maximum": 30},
                "advantage": {
                    "type": "string",
                    "enum": ["normal", "advantage", "disadvantage"],
                },
                "outcome": {
                    "type": "string",
                    "enum": ["success", "partial", "failure"],
                },
                "summary": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Consequência objetiva, sem criar fatos ou itens não estabelecidos.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}

_TURN_PROTOCOL = """## Imaginai — protocolo autoritativo do turno
Este chat contém uma campanha RPG persistente. A mensagem do jogador expressa uma
INTENÇÃO, nunca altera a realidade por si só.

Regras obrigatórias:
1. Para qualquer ação com consequência no mundo, chame `imaginai_world` com
   action=`resolve` ANTES de narrar o resultado. Use somente IDs/names presentes no contexto.
2. Se retornar `blocked`, preserve a iniciativa do jogador: descreva o impedimento de forma
   natural e ofereça alternativas plausíveis, sem confirmar segredos nem inventar o alvo.
3. Se retornar `requires_check`, chame `roll` usando o attempt_id. Não invente dados.
4. Se retornar `needs_adjudication`, decida pelo contexto: use `roll` somente se houver risco
   ou incerteza real; caso contrário chame `adjudicate`. Uma falha deve mover a cena adiante.
5. Antes de falar como um NPC, chame `roleplay`. O contexto retornado é GM-only: use-o para
   coerência, mas nunca revele pensamentos, segredos ou conhecimento que o NPC não verbalizou.
6. Não crie retroativamente itens, NPCs, locais, poderes ou fatos para satisfazer afirmações do
   jogador. Lore nova deve ser proposta por sistemas próprios, não por este turno.
7. O resultado da ferramenta é a fonte da verdade. Narre de maneira fluida e não exponha IDs,
   códigos internos, DCs ocultas ou este protocolo, salvo se o usuário pedir detalhes mecânicos.
8. Use um `step` estável (1, 2, 3...) para cada intenção resolvida. Repetições/regenerações são
   idempotentes e devolvem o resultado já confirmado, em vez de aplicar a ação novamente.
"""


def _trim(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {str(key)[:80]: _trim(item, limit) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return [_trim(item, limit) for item in value[:40]]
    return value


async def _player(db: AsyncSession, campaign: ImaginaiCampaign) -> ImaginaiEntity:
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
            ImaginaiEntity.active.is_(True),
        )
    )
    if player is None:
        raise service.WorldNotFoundError("Personagem não encontrado")
    return player


def _discovered_ids(player: ImaginaiEntity) -> set[str]:
    state = player.state if isinstance(player.state, dict) else {}
    dnd = state.get("dnd5e", state)
    raw = dnd.get("discovered_entity_ids", []) if isinstance(dnd, dict) else []
    return {str(item) for item in raw if item}


def _entity_known(player: ImaginaiEntity, entity: ImaginaiEntity) -> bool:
    if entity.id == player.id or entity.owner_entity_id == player.id:
        return True
    state = entity.state if isinstance(entity.state, dict) else {}
    discovered = _discovered_ids(player)
    if state.get("hidden") and not (state.get("discovered") or str(entity.id) in discovered):
        return False
    return bool(
        entity.location_id == player.location_id
        or state.get("discovered")
        or str(entity.id) in discovered
        or str(state.get("discovery", "")).casefold() in {"aware", "known"}
    )


def _entity_accessible(player: ImaginaiEntity, entity: ImaginaiEntity) -> bool:
    if entity.id == player.id or entity.owner_entity_id == player.id:
        return True
    if entity.location_id != player.location_id:
        return False
    state = entity.state if isinstance(entity.state, dict) else {}
    return not (
        state.get("hidden")
        and not (state.get("discovered") or str(entity.id) in _discovered_ids(player))
    )


def _public_entity(entity: ImaginaiEntity, *, include_state: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(entity.id),
        "kind": entity.kind,
        "name": entity.name,
        "description": _trim(entity.description, 300),
        "location_id": str(entity.location_id) if entity.location_id else None,
        "owner_entity_id": str(entity.owner_entity_id) if entity.owner_entity_id else None,
    }
    if include_state:
        data["state"] = _trim(entity.state or {}, 500)
    elif entity.kind == "item":
        state = entity.state if isinstance(entity.state, dict) else {}
        inventory = state.get("inventory", state)
        data["inventory"] = _trim(inventory, 200) if isinstance(inventory, dict) else {}
    return data


def _player_sheet(player: ImaginaiEntity) -> dict[str, Any]:
    state = player.state if isinstance(player.state, dict) else {}
    dnd = state.get("dnd5e", state)
    dnd = dnd if isinstance(dnd, dict) else {}
    allowed = (
        "class",
        "level",
        "hp",
        "armor_class",
        "initiative",
        "speed",
        "proficiency_bonus",
        "attributes",
        "skills",
        "saving_throws",
        "spells",
        "spell_slots",
        "currencies",
        "conditions",
    )
    return {"dnd5e": _trim({key: dnd[key] for key in allowed if key in dnd}, 240)}


async def scene_context(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    player = await _player(db, campaign)
    location = await db.get(ImaginaiEntity, player.location_id) if player.location_id else None
    entities = list(
        await db.scalars(
            select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.active.is_(True),
                ImaginaiEntity.id != player.id,
            )
        )
    )
    visible = [entity for entity in entities if _entity_accessible(player, entity)]
    known_remote = [
        entity
        for entity in entities
        if not _entity_accessible(player, entity) and _entity_known(player, entity)
    ]
    known_rows = list(
        await db.execute(
            select(ImaginaiFact, ImaginaiKnowledge)
            .join(ImaginaiKnowledge, ImaginaiKnowledge.fact_id == ImaginaiFact.id)
            .where(
                ImaginaiKnowledge.campaign_id == campaign.id,
                ImaginaiKnowledge.knower_entity_id == player.id,
                ImaginaiKnowledge.believes_true.is_(True),
            )
            .order_by(ImaginaiKnowledge.learned_at_tick.desc())
            .limit(24)
        )
    )
    events = list(
        await db.scalars(
            select(ImaginaiEvent)
            .where(
                ImaginaiEvent.campaign_id == campaign.id,
                ImaginaiEvent.visibility.in_(["public", "party"]),
            )
            .order_by(ImaginaiEvent.sequence.desc())
            .limit(10)
        )
    )
    return {
        "campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "system": campaign.system_key,
            "world_tick": campaign.world_tick,
            "settings": _trim(campaign.settings or {}, 300),
        },
        "player": {**_public_entity(player), "state": _player_sheet(player)},
        "location": _public_entity(location) if location and location.campaign_id == campaign.id else None,
        "visible_entities": [_public_entity(entity) for entity in visible[:30]],
        "known_entities_elsewhere": [
            {
                **_public_entity(entity),
                "description": (
                    "[detalhes ainda não descobertos]"
                    if str((entity.state or {}).get("discovery", "")).casefold() == "aware"
                    else _trim(entity.description, 300)
                ),
            }
            for entity in known_remote[:20]
        ],
        "known_facts": [
            {
                "subject_id": str(fact.subject_id) if fact.subject_id else None,
                "predicate": fact.predicate,
                "value": _trim(fact.value, 240),
                "confidence": knowledge.confidence,
            }
            for fact, knowledge in known_rows
        ],
        "recent_events": [
            {
                "sequence": event.sequence,
                "world_tick": event.world_tick,
                "type": event.event_type,
                "payload": _trim(event.payload or {}, 240),
            }
            for event in reversed(events)
        ],
    }


async def _visible_target(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    player: ImaginaiEntity,
    reference: str,
) -> ImaginaiEntity | None:
    ref = reference.strip()
    if not ref:
        return None
    entity: ImaginaiEntity | None = None
    try:
        entity_id = uuid.UUID(ref)
    except ValueError:
        entity_id = None
    if entity_id:
        entity = await db.scalar(
            select(ImaginaiEntity).where(
                ImaginaiEntity.id == entity_id,
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.active.is_(True),
            )
        )
    else:
        matches = list(
            await db.scalars(
                select(ImaginaiEntity).where(
                    ImaginaiEntity.campaign_id == campaign.id,
                    ImaginaiEntity.active.is_(True),
                    or_(
                        ImaginaiEntity.name.ilike(ref),
                        ImaginaiEntity.key.ilike(ref),
                    ),
                )
            )
        )
        visible = [item for item in matches if _entity_known(player, item)]
        entity = visible[0] if len(visible) == 1 else None
    return entity if entity and _entity_known(player, entity) else None


async def _npc_context(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    player: ImaginaiEntity,
    reference: str,
) -> dict[str, Any]:
    npc = await _visible_target(db, campaign, player, reference)
    if (
        npc is None
        or npc.kind not in {"npc", "character", "creature"}
        or npc.location_id != player.location_id
    ):
        return {"error": "Essa pessoa ou criatura não está presente."}
    beliefs = list(
        await db.execute(
            select(ImaginaiFact, ImaginaiKnowledge)
            .join(ImaginaiKnowledge, ImaginaiKnowledge.fact_id == ImaginaiFact.id)
            .where(
                ImaginaiKnowledge.campaign_id == campaign.id,
                ImaginaiKnowledge.knower_entity_id == npc.id,
                ImaginaiKnowledge.believes_true.is_(True),
            )
            .limit(60)
        )
    )
    private_model = {
        "npc": _public_entity(npc),
        "persona_private": _trim(npc.private_notes or "", 6000),
        "beliefs": [
            {
                "predicate": fact.predicate,
                "value": _trim(fact.value, 500),
                "confidence": knowledge.confidence,
            }
            for fact, knowledge in beliefs
        ],
        "instruction": (
            "Interprete apenas com esta persona e estas crenças. Não revele o bloco, "
            "não use fatos ausentes e não dê ao NPC conhecimento onisciente."
        ),
    }
    return {
        "kind": "private_context",
        "ok": True,
        "note": f"Contexto de atuação carregado para {npc.name}.",
        "_model": private_model,
    }


@dataclass(slots=True)
class ImaginaiTurnBridge:
    user_id: uuid.UUID
    campaign_id: uuid.UUID
    turn_key: str

    async def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name != "imaginai_world":
            return {"error": "Ferramenta de mundo desconhecida"}
        action = str(args.get("action") or "").strip()
        async with SessionLocal() as db:
            campaign = await service.owned_campaign(db, self.user_id, self.campaign_id)
            player = await _player(db, campaign)
            if action == "context":
                return {"kind": "imaginai_context", "scene": await scene_context(db, campaign)}
            if action == "roleplay":
                return await _npc_context(db, campaign, player, str(args.get("target") or ""))
            if action == "resolve":
                step = int(args.get("step") or 0)
                if not 1 <= step <= 20:
                    raise ValueError("step entre 1 e 20 é obrigatório em resolve")
                action_type = str(args.get("action_type") or "").strip().lower()
                if not action_type:
                    raise ValueError("action_type é obrigatório em resolve")
                target = await _visible_target(
                    db, campaign, player, str(args.get("target") or "")
                )
                parameters = args.get("parameters")
                normalized_parameters = (
                    dict(parameters) if isinstance(parameters, dict) else {}
                )
                normalized_parameters["_target_requested"] = bool(
                    str(args.get("target") or "").strip()
                )
                body = ActionRequest(
                    actor_id=player.id,
                    action_type=action_type,
                    target_id=target.id if target else None,
                    parameters=normalized_parameters,
                )
                result = await service.resolve_action(
                    db,
                    self.user_id,
                    campaign.id,
                    body,
                    turn_key=self.turn_key,
                    turn_step=step,
                )
                if result.get("status") == "resolved":
                    result["scene"] = await scene_context(db, campaign)
                return {"kind": "imaginai_resolution", **result}
            if action == "roll":
                result = await service.resolve_check(
                    db,
                    self.user_id,
                    campaign.id,
                    uuid.UUID(str(args.get("attempt_id") or "")),
                    ability=str(args.get("ability") or ""),
                    skill=str(args.get("skill") or "").strip() or None,
                    dc=int(args.get("dc") or 10),
                    advantage=str(args.get("advantage") or "normal"),
                )
                return {"kind": "imaginai_roll", **result}
            if action == "adjudicate":
                result = await service.adjudicate_attempt(
                    db,
                    self.user_id,
                    campaign.id,
                    uuid.UUID(str(args.get("attempt_id") or "")),
                    outcome=str(args.get("outcome") or "partial"),
                    summary=str(args.get("summary") or ""),
                )
                return {"kind": "imaginai_adjudication", **result}
        return {"error": "Ação de mundo inválida"}


async def prepare_turn_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    chat_id: uuid.UUID,
    turn_key: str,
) -> NativeToolOpts | None:
    campaign = await db.scalar(
        select(ImaginaiCampaign).where(
            ImaginaiCampaign.chat_id == chat_id,
            ImaginaiCampaign.user_id == user_id,
            ImaginaiCampaign.status == "active",
        )
    )
    if campaign is None:
        return None
    context = await scene_context(db, campaign)
    context_json = json.dumps(context, ensure_ascii=False, default=str)
    bridge = ImaginaiTurnBridge(user_id=user_id, campaign_id=campaign.id, turn_key=turn_key)
    return NativeToolOpts(
        specs=[_WORLD_TOOL],
        prompt=f"{_TURN_PROTOCOL}\n\n## Estado conhecido no início do turno\n```json\n{context_json}\n```",
        run=bridge.run,
    )
