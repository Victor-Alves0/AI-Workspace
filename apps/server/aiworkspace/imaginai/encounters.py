"""Combate por turnos — a parte que toca o banco.

`combat.py` decide as regras (iniciativa, ataque inimigo, fim do combate) sem banco.
Aqui só se carrega quem luta, chama o núcleo e persiste o que ele devolveu: o HP do
jogador, um evento por turno inimigo e o estado do encontro na campanha.

Tudo roda DENTRO da transação da ação do jogador (a campanha já está travada pelo
chamador), então o turno do jogador e a reação dos inimigos são gravados juntos — ou
nenhum dos dois.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ImaginaiCampaign, ImaginaiEntity, ImaginaiEvent
from . import combat

_COMBATANT_KINDS = ("npc", "creature", "character")


def is_player(entity: ImaginaiEntity | None) -> bool:
    return bool(entity is not None and entity.kind == "character" and entity.key == "player")


def active(campaign: ImaginaiCampaign) -> bool:
    enc = campaign.encounter
    return bool(isinstance(enc, dict) and enc.get("active"))


def _fighter(entity: ImaginaiEntity, side: str) -> combat.Fighter | None:
    return combat.fighter_from(
        str(entity.id), entity.name, entity.state,
        str(entity.location_id) if entity.location_id else None, side,
    )


async def _hostiles_here(
    db: AsyncSession, campaign: ImaginaiCampaign, player: ImaginaiEntity,
) -> list[ImaginaiEntity]:
    """Inimigos que entram no combate: hostis, ativos, de pé e no MESMO local."""
    if player.location_id is None:
        return []
    rows = await db.scalars(
        select(ImaginaiEntity)
        .where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.location_id == player.location_id,
            ImaginaiEntity.kind.in_(_COMBATANT_KINDS),
            ImaginaiEntity.active.is_(True),
            ImaginaiEntity.id != player.id,
        )
        .with_for_update()
    )
    out = []
    for entity in rows:
        vida = combat.hp_of(entity.state)
        if combat.is_hostile(entity.state) and vida is not None and vida[0] > 0:
            out.append(entity)
    return out


def _mark_hostile(entity: ImaginaiEntity) -> None:
    """Quem é atacado revida — mesmo que tenha sido criado como neutro."""
    if combat.is_hostile(entity.state):
        return
    state = copy.deepcopy(entity.state or {})
    state["hostile"] = True
    entity.state = state


def _event(campaign: ImaginaiCampaign, user_id: uuid.UUID, event_type: str,
           actor_id, target_id, location_id, payload: dict[str, Any]) -> ImaginaiEvent:
    event = ImaginaiEvent(
        campaign_id=campaign.id, user_id=user_id,
        sequence=campaign.next_event_sequence, world_tick=campaign.world_tick,
        event_type=event_type, actor_id=actor_id, target_id=target_id,
        location_id=location_id, payload=payload, visibility="public",
    )
    campaign.next_event_sequence += 1
    return event


async def _entities_by_id(
    db: AsyncSession, campaign: ImaginaiCampaign, ids: list[str],
) -> dict[str, ImaginaiEntity]:
    wanted = []
    for raw in ids:
        try:
            wanted.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    if not wanted:
        return {}
    rows = await db.scalars(
        select(ImaginaiEntity)
        .where(ImaginaiEntity.campaign_id == campaign.id, ImaginaiEntity.id.in_(wanted))
        .with_for_update()
    )
    return {str(entity.id): entity for entity in rows}


async def _run(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID,
    player: ImaginaiEntity, encounter: dict[str, Any],
    entities: dict[str, ImaginaiEntity],
) -> dict[str, Any]:
    """Resolve os turnos inimigos e grava: HP do jogador, eventos, estado do encontro."""
    pid = str(player.id)
    fighters: dict[str, combat.Fighter] = {}
    for eid, entity in entities.items():
        lutador = _fighter(entity, "player" if eid == pid else "hostile")
        if lutador is not None:
            fighters[eid] = lutador

    report = combat.run_enemy_turns(encounter, fighters, pid, combat.secure_roller)

    antes = fighters[pid].hp if pid in fighters else None
    depois = report.fighters[pid].hp if pid in report.fighters else None
    if antes is not None and depois is not None and depois != antes:
        from .service import _apply_mutation   # tardio: service importa este módulo
        _apply_mutation(
            {"op": "adjust_hp", "entity_id": pid, "amount": depois - antes}, {pid: player},
        )
    for turno in report.enemy_turns:
        db.add(_event(
            campaign, user_id, "enemy_attack", uuid.UUID(turno["attacker_id"]), player.id,
            player.location_id, turno,
        ))
    if not report.encounter.get("active") and report.encounter.get("outcome"):
        db.add(_event(
            campaign, user_id, "encounter_ended", player.id, None, player.location_id,
            {"outcome": report.encounter["outcome"], "round": report.encounter.get("round")},
        ))
    campaign.encounter = report.encounter
    return {
        **(combat.public_view(report.encounter, report.fighters, pid) or {}),
        "enemy_turns": report.enemy_turns,
    }


async def start(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID,
    player: ImaginaiEntity, *, target: ImaginaiEntity | None = None,
    player_already_acted: bool,
) -> dict[str, Any] | None:
    """Abre um encontro: rola iniciativa e já resolve os inimigos que agem antes do
    jogador. Sem inimigo de pé no local, não há combate (devolve None)."""
    inimigos = await _hostiles_here(db, campaign, player)
    if target is not None and not is_player(target):
        vida = combat.hp_of(target.state)
        if vida is not None and vida[0] > 0 and target.location_id == player.location_id:
            _mark_hostile(target)
            if all(e.id != target.id for e in inimigos):
                inimigos.append(target)
    if not inimigos:
        return None

    entidades = {str(player.id): player, **{str(e.id): e for e in inimigos}}
    lutadores = [
        lutador for eid, entity in entidades.items()
        if (lutador := _fighter(entity, "player" if eid == str(player.id) else "hostile"))
    ]
    if not any(l.side == "player" for l in lutadores):
        return None          # personagem sem ficha de combate: nada a disputar
    encontro = combat.start(
        lutadores, str(player.id), combat.secure_roller,
        player_already_acted=player_already_acted,
    )
    db.add(_event(
        campaign, user_id, "encounter_started", player.id, None, player.location_id,
        {"order": encontro["order"], "ambush": not player_already_acted},
    ))
    return await _run(db, campaign, user_id, player, encontro, entidades)


async def after_player_turn(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID,
    player: ImaginaiEntity,
) -> dict[str, Any] | None:
    """O jogador gastou o turno: os inimigos agem até a vez dele voltar."""
    if not active(campaign):
        return None
    encontro = copy.deepcopy(campaign.encounter)
    ids = [entry.get("id") for entry in encontro.get("order") or []]
    entidades = await _entities_by_id(db, campaign, ids)
    entidades[str(player.id)] = player           # o objeto já travado pelo chamador
    encontro = combat.end_player_turn(encontro, str(player.id))
    return await _run(db, campaign, user_id, player, encontro, entidades)


async def summary(
    db: AsyncSession, campaign: ImaginaiCampaign, player: ImaginaiEntity,
) -> dict[str, Any] | None:
    """Encontro como o jogador o vê (painel e contexto do turno). Sem combate: None."""
    enc = campaign.encounter
    if not isinstance(enc, dict) or not enc.get("order"):
        return None
    ids = [entry.get("id") for entry in enc["order"]]
    entidades = await db.scalars(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.id.in_([uuid.UUID(str(i)) for i in ids if i]),
        )
    )
    pid = str(player.id)
    lutadores = {}
    for entity in entidades:
        lutador = _fighter(entity, "player" if str(entity.id) == pid else "hostile")
        if lutador is not None:
            lutadores[str(entity.id)] = lutador
    return combat.public_view(enc, lutadores, pid)
