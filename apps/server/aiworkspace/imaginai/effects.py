"""Efeitos do mundo fora do ataque comum — tudo rolado e gravado no servidor.

O narrador pede; o servidor rola e aplica. Nada aqui aceita número "decidido" pelo
modelo: dano vem em DADOS (rolados aqui), resistência é d20 + bônus da ficha, e o que
muda (PV, condição, lado no combate, destino do personagem) vira evento público.

    saving_throw  teste de resistência de qualquer entidade (armadilha, magia, veneno)
    apply_effect  dano/cura/condição de uma fonte fora do combate por turnos
    set_ally      NPC passa a lutar ao lado do jogador (ou deixa de lutar)
    decide_fate   o personagem caiu a 0 PV: morre, estabiliza, é capturado, resgatado…
    new_character depois da morte: o antigo vira um falecido do mundo e começa outro
    roll_dice     rolagem livre (tabela aleatória, sorte, "quantos guardas?")
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import dice
from ..models import ImaginaiCampaign, ImaginaiEntity, ImaginaiEvent
from . import combat, encounters
from .images import find_entity

FATES = ("dead", "stabilized", "captured", "rescued", "revived")


class EffectError(ValueError):
    """Pedido inválido — a mensagem volta ao narrador."""


def _dnd(entity: ImaginaiEntity) -> dict[str, Any]:
    return combat.dnd_state(entity.state)


def player_status(player: ImaginaiEntity | None) -> str:
    """"alive" | "down" (0 PV, destino pendente) | "dead"."""
    if player is None:
        return "alive"
    dnd = _dnd(player)
    if dnd.get("dead"):
        return "dead"
    vida = combat.hp_of(player.state)
    return "down" if vida is not None and vida[0] <= 0 else "alive"


def _event(campaign: ImaginaiCampaign, user_id: uuid.UUID, event_type: str,
           target: ImaginaiEntity | None, payload: dict[str, Any]) -> ImaginaiEvent:
    event = ImaginaiEvent(
        campaign_id=campaign.id, user_id=user_id, sequence=campaign.next_event_sequence,
        world_tick=campaign.world_tick, event_type=event_type,
        actor_id=None, target_id=target.id if target else None,
        location_id=target.location_id if target else None, payload=payload, visibility="public",
    )
    campaign.next_event_sequence += 1
    return event


async def _target(db: AsyncSession, campaign: ImaginaiCampaign, reference: str) -> ImaginaiEntity:
    entity = await find_entity(db, campaign, reference)
    if entity is None:
        raise EffectError(f"Entidade não encontrada: {reference!r}. Use o nome exato (ou 'player').")
    return entity


def _fighter(entity: ImaginaiEntity) -> combat.Fighter:
    """Combatente para rolar resistência. Sem PV cadastrado, ainda dá para resistir."""
    lutador = combat.fighter_from(str(entity.id), entity.name, entity.state, None, "hostile")
    if lutador is not None:
        return lutador
    return combat.Fighter(
        id=str(entity.id), name=entity.name, side="hostile", hp=1, hp_max=1,
        ac=combat.armor_class(entity.state), location_id=None,
        conditions=combat.conditions_of(entity.state), saves=combat.save_modifiers(entity.state),
    )


async def saving_throw(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID,
    target_ref: str, ability: str, dc: int, advantage: str = "normal",
    roller: combat.Roller = combat.secure_roller,
) -> dict[str, Any]:
    entity = await _target(db, campaign, target_ref)
    chave = combat.ability_key(ability)
    if chave is None:
        raise EffectError("ability: FOR, DES, CON, INT, SAB ou CAR.")
    modo = advantage if advantage in {"normal", "advantage", "disadvantage"} else "normal"
    resultado = combat.roll_save(_fighter(entity), chave, max(5, min(int(dc), 30)), roller, modo)
    db.add(_event(campaign, user_id, "saving_throw", entity, {**resultado, "target": entity.name}))
    await db.commit()
    return {"target": entity.name, **resultado}


async def apply_effect(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, target_ref: str, *,
    damage: str | None = None, damage_type: str = "", healing: str | None = None,
    half: bool = False, condition: str | None = None, rounds: int | None = None,
    remove_condition: str | None = None, reason: str = "",
    roller: dice.Roller = dice.secure_roller,
) -> dict[str, Any]:
    """Dano e cura em DADOS (rolados aqui); condição aplicada/removida. `half` = o alvo
    passou na resistência e leva metade."""
    from .service import _apply_mutation

    entity = await _target(db, campaign, target_ref)
    out: dict[str, Any] = {"target": entity.name, "reason": reason[:300]}
    delta = 0
    try:
        if damage:
            rolagem = dice.roll(str(damage), roller)
            valor = max(0, rolagem.total)
            if half:
                valor //= 2
            out["damage"] = {"roll": rolagem.as_dict(), "amount": valor, "type": damage_type[:40], "half": half}
            delta -= valor
        if healing:
            rolagem = dice.roll(str(healing), roller)
            out["healing"] = {"roll": rolagem.as_dict(), "amount": max(0, rolagem.total)}
            delta += max(0, rolagem.total)
    except dice.DiceError as exc:
        raise EffectError(str(exc)) from exc
    vida = combat.hp_of(entity.state)
    if delta:
        if vida is None:
            raise EffectError(f"{entity.name} não tem pontos de vida cadastrados.")
        _apply_mutation({"op": "adjust_hp", "entity_id": str(entity.id), "amount": delta},
                        {str(entity.id): entity})
    condicoes = combat.conditions_of(entity.state)
    if remove_condition:
        chave = combat.condition_key(remove_condition)
        condicoes = tuple(c for c in condicoes if c["key"] != chave)
        out["removed_condition"] = chave
    if condition:
        chave = combat.condition_key(condition)
        if chave is None:
            raise EffectError(f"Condição desconhecida: {condition}. Use: {', '.join(combat.CONDITIONS)}.")
        condicoes = combat.add_condition(condicoes, chave, max(1, min(int(rounds), 100)) if rounds else None)
        out["condition"] = {"key": chave, "rounds": rounds}
    nova_vida = combat.hp_of(entity.state)
    if nova_vida is not None and nova_vida[0] <= 0 and (vida is None or vida[0] > 0):
        condicoes = combat.add_condition(condicoes, "unconscious", None)
        out["dropped"] = True
    if nova_vida is not None and nova_vida[0] > 0:
        condicoes = tuple(c for c in condicoes if c["key"] != "unconscious")
    if condicoes != combat.conditions_of(entity.state):
        encounters.write_conditions(entity, condicoes)
    if nova_vida is not None:
        out["hp_after"] = nova_vida[0] if encounters.is_player(entity) or combat.is_ally(entity.state) \
            else combat.health_label(*nova_vida)
    db.add(_event(campaign, user_id, "effect_applied", entity, out))
    await db.commit()
    if encounters.is_player(entity) and player_status(entity) == "down":
        out["player_down"] = "O personagem caiu a 0 PV: decida o destino com `fate`."
    return out


async def set_ally(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, target_ref: str, join: bool,
) -> dict[str, Any]:
    entity = await _target(db, campaign, target_ref)
    if encounters.is_player(entity) or entity.kind not in {"npc", "creature"}:
        raise EffectError("Só NPCs e criaturas podem se aliar.")
    state = copy.deepcopy(entity.state or {})
    state["ally"] = bool(join)
    if join:
        state["hostile"] = False
        if isinstance(state.get("dnd5e"), dict):
            state["dnd5e"]["hostile"] = False
    entity.state = state
    db.add(_event(campaign, user_id, "ally_joined" if join else "ally_left", entity, {"target": entity.name}))
    await db.commit()
    lutador = combat.fighter_from(str(entity.id), entity.name, entity.state, None, "ally")
    return {"target": entity.name, "ally": bool(join),
            "can_fight": lutador is not None,
            "note": None if lutador is not None else "Sem PV cadastrados: não entra no combate por turnos."}


async def _player(db: AsyncSession, campaign: ImaginaiCampaign) -> ImaginaiEntity:
    player = await db.scalar(select(ImaginaiEntity).where(
        ImaginaiEntity.campaign_id == campaign.id,
        ImaginaiEntity.kind == "character", ImaginaiEntity.key == "player",
    ).with_for_update())
    if player is None:
        raise EffectError("Personagem não encontrado.")
    return player


async def decide_fate(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, outcome: str, summary: str,
) -> dict[str, Any]:
    """O narrador decide, pela história, o que acontece com quem caiu (ou morreu)."""
    if outcome not in FATES:
        raise EffectError(f"outcome: {', '.join(FATES)}.")
    player = await _player(db, campaign)
    status = player_status(player)
    if outcome == "revived":
        if status != "dead":
            raise EffectError("`revived` é para trazer de volta quem MORREU.")
    elif status != "down":
        raise EffectError("O personagem não está caído (0 PV).")
    dnd = copy.deepcopy(_dnd(player))
    hp = dict(dnd.get("hp") or {})
    if outcome == "dead":
        dnd["dead"] = True
        hp["current"] = 0
    else:
        dnd["dead"] = False
        hp["current"] = max(1, int(hp.get("current") or 0))
        dnd["conditions"] = [c for c in dnd.get("conditions") or []
                             if combat.condition_key((c or {}).get("key") if isinstance(c, dict) else c) != "unconscious"]
    dnd["hp"] = hp
    state = copy.deepcopy(player.state or {})
    if isinstance(state.get("dnd5e"), dict):
        state["dnd5e"] = dnd
    else:
        state.update(dnd)
    player.state = state
    if isinstance(campaign.encounter, dict) and campaign.encounter.get("active"):
        campaign.encounter = {**campaign.encounter, "active": False, "outcome": "defeat"}
    db.add(_event(campaign, user_id, "character_fate", player,
                  {"outcome": outcome, "summary": summary[:2000], "name": player.name}))
    await db.commit()
    resposta: dict[str, Any] = {"outcome": outcome, "character": player.name}
    if outcome == "dead":
        resposta["next"] = (
            "Narre a morte. Depois pergunte ao jogador se quer continuar: com a MESMA ficha "
            "(fate=revived, se a história permitir) ou com um personagem novo (new_character)."
        )
    return resposta


async def new_character(db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID) -> dict[str, Any]:
    """Depois da morte: o personagem antigo fica no mundo como um falecido (com os
    itens e a história) e um novo nasce no mesmo lugar, pela sessão zero de personagem."""
    from .service import default_character_state
    from .setup import slug

    player = await _player(db, campaign)
    if player_status(player) != "dead":
        raise EffectError("Só depois da morte do personagem atual.")
    taken = set(await db.scalars(select(ImaginaiEntity.key).where(ImaginaiEntity.campaign_id == campaign.id)))
    antigo_nome = player.name
    descobertos = list(_dnd(player).get("discovered_entity_ids") or [])
    state = copy.deepcopy(player.state or {})
    state["discovered"] = True
    player.key = slug(f"falecido-{antigo_nome}", taken)
    player.kind = "npc"
    player.state = state
    player.description = (player.description or "") + f"\n{antigo_nome} caiu aqui." if player.description else f"{antigo_nome}, um aventureiro que caiu."
    await db.flush()

    novo_state = default_character_state()
    novo_state["dnd5e"]["discovered_entity_ids"] = descobertos   # o jogador não perde o que já sabe
    db.add(ImaginaiEntity(
        campaign_id=campaign.id, user_id=user_id, kind="character", key="player",
        name="Nome do personagem", location_id=player.location_id, state=novo_state,
    ))
    campaign.setup_stage = "character"
    campaign.encounter = None
    db.add(_event(campaign, user_id, "character_replaced", player, {"fallen": antigo_nome}))
    await db.commit()
    return {"stage": "character", "fallen": antigo_nome,
            "next": "Conduza a criação do novo personagem (imaginai_setup) e depois begin_adventure."}


async def roll_dice(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, expression: str, reason: str = "",
    roller: dice.Roller = dice.secure_roller,
) -> dict[str, Any]:
    try:
        resultado = dice.roll(expression, roller)
    except dice.DiceError as exc:
        raise EffectError(str(exc)) from exc
    payload = {**resultado.as_dict(), "reason": reason[:300]}
    db.add(_event(campaign, user_id, "dice_rolled", None, payload))
    await db.commit()
    return payload
