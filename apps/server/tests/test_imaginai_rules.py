"""Invariantes do World Kernel: intenção do jogador nunca cria realidade."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace.chat.orchestrator import (
    NativeToolOpts,
    _assemble_tools_and_prompt,
    _shape_tool_result,
)
from aiworkspace.imaginai.rules import ActionIntent, EntitySnapshot, ruleset_for
from aiworkspace.imaginai.service import (
    WorldConflictError,
    _apply_mutation,
    _merge_character_setup,
    _roll_damage,
    _spells_from_state,
)
from aiworkspace.imaginai.systems import system_definition
from aiworkspace.schemas.imaginai import CampaignUpdate, CharacterUpdate, JournalCreate


def entity(
    ident: str,
    *,
    kind: str,
    location: str | None = "village",
    owner: str | None = None,
    state: dict | None = None,
) -> EntitySnapshot:
    return EntitySnapshot(
        id=ident,
        kind=kind,
        key=ident,
        name=ident.title(),
        location_id=location,
        owner_entity_id=owner,
        state=state or {},
    )


def test_player_assertion_does_not_create_a_legendary_sword():
    actor = entity("hero", kind="character")
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="take_item", actor=actor, target=None)
    )
    assert decision.allowed is False
    assert decision.reason_code == "target_not_accessible"


def test_hidden_item_does_not_leak_its_existence():
    actor = entity(
        "hero",
        kind="character",
        state={"dnd5e": {"discovered_entity_ids": []}},
    )
    sword = entity("secret-sword", kind="item", state={"hidden": True})
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="take_item", actor=actor, target=sword)
    )
    assert decision.allowed is False
    assert decision.reason_code == "target_not_accessible"
    assert "espada" not in decision.reason.casefold()


def test_known_item_can_be_taken_only_when_present():
    actor = entity(
        "hero",
        kind="character",
        state={"dnd5e": {"discovered_entity_ids": ["sword"]}},
    )
    sword = entity("sword", kind="item", state={"hidden": True})
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="take_item", actor=actor, target=sword)
    )
    assert decision.allowed is True
    assert {m["op"] for m in decision.mutations} == {"set_owner", "set_location"}

    remote_sword = entity("sword", kind="item", location="other-place")
    remote = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="take_item", actor=actor, target=remote_sword)
    )
    assert remote.allowed is False


def test_remote_npc_cannot_be_addressed_as_if_present():
    actor = entity("hero", kind="character", location="village")
    mark = entity("mark", kind="npc", location="mountains-500km-away")
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="interact", actor=actor, target=mark)
    )
    assert decision.allowed is False
    assert decision.reason_code == "target_not_present"


def test_unknown_spell_is_rejected_even_if_player_names_it():
    actor = entity(
        "hero",
        kind="character",
        state={"dnd5e": {"spells": [], "spell_slots": {"3": {"current": 2, "max": 2}}}},
    )
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="cast_spell",
            actor=actor,
            parameters={"spell": "Bola de Fogo", "spell_level": 3},
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "spell_not_known"


def test_known_prepared_spell_consumes_authoritative_slot():
    actor = entity(
        "hero",
        kind="character",
        state={
            "dnd5e": {
                "spells": [
                    {
                        "key": "fireball",
                        "name": "Bola de Fogo",
                        "level": 3,
                        "known": True,
                        "prepared": True,
                    }
                ],
                "spell_slots": {"3": {"current": 1, "max": 2}},
            }
        },
    )
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="cast_spell",
            actor=actor,
            parameters={"spell": "Bola de Fogo", "spell_level": 9},
        )
    )
    assert decision.allowed is True
    assert decision.public_payload["level"] == 3
    assert decision.mutations == (
        {
            "op": "decrement_spell_slot",
            "entity_id": "hero",
            "level": 3,
            "amount": 1,
            "when": "always",
        },
    )


def test_spell_without_slot_is_rejected():
    actor = entity(
        "hero",
        kind="character",
        state={
            "dnd5e": {
                "spells": [{"key": "fireball", "name": "Bola de Fogo", "level": 3}],
                "spell_slots": {"3": {"current": 0, "max": 2}},
            }
        },
    )
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="cast_spell", actor=actor, parameters={"spell": "fireball"})
    )
    assert decision.reason_code == "spell_slot_unavailable"


def test_spell_cannot_target_a_remote_creature():
    actor = entity(
        "hero",
        kind="character",
        location="village",
        state={
            "dnd5e": {
                "spells": [{"key": "fireball", "name": "Bola de Fogo", "level": 3}],
                "spell_slots": {"3": {"current": 1, "max": 1}},
            }
        },
    )
    remote_target = entity("mark", kind="npc", location="mountains")
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="cast_spell",
            actor=actor,
            target=remote_target,
            parameters={"spell": "Bola de Fogo", "_target_requested": True},
        )
    )

    assert decision.allowed is False
    assert decision.reason_code == "target_not_present"


def test_spell_attack_uses_sheet_modifier_and_effect_instead_of_player_claims():
    actor = entity(
        "hero",
        kind="character",
        state={
            "dnd5e": {
                "spell_attack_modifier": 6,
                "spells": [
                    {
                        "key": "fire_bolt",
                        "name": "Raio de Fogo",
                        "level": 0,
                        "effect": {
                            "roll_kind": "spell_attack",
                            "damage": "1d10",
                            "damage_type": "fire",
                        },
                    }
                ],
            }
        },
    )
    target = entity(
        "goblin",
        kind="creature",
        state={"dnd5e": {"hp": {"current": 7, "max": 7}, "armor_class": 13}},
    )

    decision = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="cast_spell",
            actor=actor,
            target=target,
            parameters={
                "spell": "Raio de Fogo",
                "_target_requested": True,
                "attack_modifier": 99,
                "damage": "99d99",
            },
        )
    )

    assert decision.status == "requires_check"
    assert decision.public_payload["roll_kind"] == "spell_attack"
    assert decision.public_payload["attack_modifier"] == 6
    assert decision.public_payload["target_ac"] == 13
    assert decision.public_payload["damage"] == "1d10"


def test_unknown_ruleset_fails_closed():
    with pytest.raises(ValueError, match="não suportado"):
        ruleset_for("player-invented-system")


def test_creative_action_is_adjudicated_instead_of_rejected():
    actor = entity("hero", kind="character")
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="swing_from_chandelier",
            actor=actor,
            parameters={"goal": "cruzar o salão e derrubar o guarda"},
        )
    )

    assert decision.allowed is True
    assert decision.status == "needs_adjudication"
    assert decision.resolution == "narrative"
    assert decision.reason_code == "creative_action"
    assert decision.consumes_turn is True


def test_hard_contradiction_is_distinct_from_narrative_adjudication():
    actor = entity("hero", kind="character")
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="take_item", actor=actor, target=None)
    )

    assert decision.allowed is False
    assert decision.status == "blocked"
    assert decision.resolution == "contradiction"


def test_obvious_examination_does_not_force_a_roll():
    actor = entity("hero", kind="character")
    statue = entity("statue", kind="object", state={"material": "stone"})
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="examine", actor=actor, target=statue)
    )

    assert decision.status == "allowed"
    assert decision.resolution == "automatic"


def test_hidden_detail_requests_a_check_without_rejecting_the_action():
    actor = entity("hero", kind="character")
    statue = entity("statue", kind="object", state={"hidden": True})
    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="examine", actor=actor, target=statue)
    )

    assert decision.allowed is True
    assert decision.status == "requires_check"
    assert decision.resolution == "check"


def test_known_route_moves_character_without_inventing_a_destination():
    actor = entity("hero", kind="character", location="village")
    forest = entity(
        "forest",
        kind="location",
        location=None,
        state={"discovered": True, "accessible_from": ["village"]},
    )

    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="move", actor=actor, target=forest)
    )

    assert decision.status == "allowed"
    assert decision.mutations == (
        {"op": "set_location", "entity_id": "hero", "location_id": "forest"},
    )


def test_known_destination_without_route_is_adjudicated_not_rejected():
    actor = entity("hero", kind="character", location="village")
    mountains = entity(
        "mountains",
        kind="location",
        location=None,
        state={"discovered": True},
    )

    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="travel", actor=actor, target=mountains)
    )

    assert decision.status == "needs_adjudication"
    assert decision.reason_code == "route_adjudication"
    assert decision.mutations[0]["op"] == "set_location"


def test_attack_uses_authoritative_attack_and_target_armor_class():
    actor = entity(
        "hero",
        kind="character",
        state={
            "dnd5e": {
                "attacks": [
                    {
                        "key": "longsword",
                        "name": "Espada longa",
                        "attack_modifier": 5,
                        "damage": "1d8+3",
                        "damage_type": "slashing",
                    }
                ]
            }
        },
    )
    goblin = entity(
        "goblin",
        kind="creature",
        state={"dnd5e": {"hp": {"current": 7, "max": 7}, "armor_class": 15}},
    )

    decision = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="attack",
            actor=actor,
            target=goblin,
            parameters={"attack": "Espada longa", "damage": "999d999"},
        )
    )

    assert decision.status == "requires_check"
    assert decision.public_payload["target_ac"] == 15
    assert decision.public_payload["damage"] == "1d8+3"


def test_attack_without_target_sheet_is_adjudicated_instead_of_fabricating_hp():
    actor = entity("hero", kind="character")
    villager = entity("villager", kind="npc")

    decision = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="attack", actor=actor, target=villager)
    )

    assert decision.status == "needs_adjudication"
    assert decision.reason_code == "target_mechanics_missing"


def test_payment_cannot_exceed_authoritative_balance():
    actor = entity(
        "hero",
        kind="character",
        state={"dnd5e": {"currencies": {"gp": 3}}},
    )
    merchant = entity("merchant", kind="npc")

    denied = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="pay_currency",
            actor=actor,
            target=merchant,
            parameters={"currency": "gp", "amount": 4},
        )
    )
    allowed = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="pay_currency",
            actor=actor,
            target=merchant,
            parameters={"currency": "gp", "amount": 2},
        )
    )

    assert denied.reason_code == "insufficient_funds"
    assert allowed.status == "allowed"
    assert [mutation["amount"] for mutation in allowed.mutations] == [-2, 2]


def test_item_cannot_be_equipped_in_a_player_invented_slot():
    actor = entity("hero", kind="character")
    armor = entity(
        "chain-mail",
        kind="item",
        owner="hero",
        location=None,
        state={"inventory": {"equippable": True, "slot": "armor"}},
    )

    denied = ruleset_for("dnd5e").validate(
        ActionIntent(
            action_type="equip_item",
            actor=actor,
            target=armor,
            parameters={"slot": "main_hand"},
        )
    )
    allowed = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="equip_item", actor=actor, target=armor)
    )

    assert denied.reason_code == "item_not_equippable"
    assert allowed.status == "allowed"
    assert allowed.public_payload["slot"] == "armor"


def test_long_rest_and_wait_advance_deterministic_world_time():
    actor = entity("hero", kind="character")

    rest = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="rest", actor=actor, parameters={"kind": "long"})
    )
    wait = ruleset_for("dnd5e").validate(
        ActionIntent(action_type="wait", actor=actor, parameters={"minutes": 90})
    )

    assert rest.status == "allowed"
    assert rest.tick_cost == 480
    assert rest.mutations[0]["op"] == "restore_long_rest"
    assert wait.tick_cost == 90


def test_resource_mutations_keep_hp_currency_and_consumables_bounded():
    actor = SimpleNamespace(
        id="hero",
        state={
            "dnd5e": {
                "hp": {"current": 8, "max": 10},
                "currencies": {"gp": 3},
                "spell_slots": {"1": {"current": 0, "max": 2}},
            }
        },
        active=True,
    )
    potion = SimpleNamespace(
        id="potion",
        state={"inventory": {"quantity": 1, "consumable": True}},
        active=True,
    )
    entities = {"hero": actor, "potion": potion}

    _apply_mutation({"op": "adjust_hp", "entity_id": "hero", "amount": 99}, entities)
    _apply_mutation(
        {"op": "adjust_currency", "entity_id": "hero", "currency": "gp", "amount": -2},
        entities,
    )
    _apply_mutation({"op": "adjust_item_quantity", "entity_id": "potion", "amount": -1}, entities)
    _apply_mutation({"op": "restore_long_rest", "entity_id": "hero"}, entities)

    assert actor.state["dnd5e"]["hp"]["current"] == 10
    assert actor.state["dnd5e"]["currencies"]["gp"] == 1
    assert actor.state["dnd5e"]["spell_slots"]["1"]["current"] == 2
    assert potion.state["inventory"]["quantity"] == 0
    assert potion.active is False


def test_damage_dice_parser_is_bounded_and_never_evaluates_input():
    damage, rolls, modifier = _roll_damage("2d6+3")

    assert len(rolls) == 2
    assert modifier == 3
    assert 5 <= damage <= 15
    with pytest.raises(WorldConflictError):
        _roll_damage("__import__('os').system('echo unsafe')")


def test_dnd5e_definition_drives_sheet_and_inventory_without_ui_hardcoding():
    definition = system_definition("dnd5e")

    assert [attribute["short"] for attribute in definition["sheet"]["attributes"]] == [
        "FOR",
        "DES",
        "CON",
        "INT",
        "SAB",
        "CAR",
    ]
    assert [currency["label"] for currency in definition["inventory"]["currencies"]] == [
        "PC",
        "PP",
        "PE",
        "PO",
        "PL",
    ]
    assert definition["inventory"]["currency_weight"]["default_enabled"] is True
    assert all(currency["weight"] == 0.02 for currency in definition["inventory"]["currencies"])
    assert definition["inventory"]["currency_weight"]["supported"] is True


def test_journal_tags_are_normalized_and_deduplicated():
    entry = JournalCreate(tags=["  missão principal ", "MISSÃO PRINCIPAL", "NPCs"])

    assert entry.tags == ["missão principal", "NPCs"]


def test_character_setup_derives_dnd5e_values_and_clamps_hp():
    state = _merge_character_setup(
        {
            "dnd5e": {
                "skills": {"perception": {"proficient": True}},
                "spells": [{"key": "light", "name": "Luz", "level": 0}],
            }
        },
        CharacterUpdate(
            character_class="Mago",
            level=5,
            hp_current=999,
            hp_max=28,
            attributes={"dexterity": 16, "wisdom": 14},
        ),
    )
    dnd = state["dnd5e"]

    assert dnd["class"] == "Mago"
    assert dnd["level"] == 5
    assert dnd["hp"] == {"current": 28, "max": 28}
    assert dnd["proficiency_bonus"] == 3
    assert dnd["initiative"] == 3
    assert dnd["passive_perception"] == 15
    assert dnd["spells"] == [{"key": "light", "name": "Luz", "level": 0}]


def test_spell_snapshot_normalizes_lists_and_preserves_preparation_state():
    spells = _spells_from_state({
        "spells": [
            {"key": "fireball", "name": "Bola de Fogo", "level": 3, "prepared": False},
            "Luz",
        ]
    })

    assert [spell["name"] for spell in spells] == ["Luz", "Bola de Fogo"]
    assert spells[0]["level"] == 0
    assert spells[1]["prepared"] is False


def test_campaign_setup_accepts_a_persistent_opening_world_seed():
    setup = CampaignUpdate(
        premise="Uma cidade suspensa sobre as nuvens.",
        opening_scene="O sino de alarme toca durante a feira.",
        starting_location_name="Mercado Celeste",
        starting_location_description="Barracas balançam ao vento.",
    )

    assert setup.starting_location_name == "Mercado Celeste"
    assert "alarme" in (setup.opening_scene or "")


def test_private_npc_context_never_reaches_ui_event():
    model_content, ui_event = _shape_tool_result(
        {
            "kind": "private_context",
            "note": "Persona carregada.",
            "_model": {"persona_private": "segredo do NPC"},
        }
    )

    assert "segredo do NPC" in model_content
    assert "segredo do NPC" not in str(ui_event)
    assert "_model" not in ui_event


def test_imaginai_native_tool_is_available_without_sift():
    spec = {
        "type": "function",
        "function": {"name": "imaginai_world", "parameters": {"type": "object"}},
    }
    native = NativeToolOpts(specs=[spec], prompt="protocolo do mundo", run=lambda *_: None)
    assembled = _assemble_tools_and_prompt(
        sift=None,
        use_tools=True,
        code_mode=False,
        skills=[],
        genimage=None,
        kb_tool_on=False,
        brain=None,
        skill_learning=False,
        subagents=[],
        run_subagent=None,
        native=native,
    )

    assert [tool["function"]["name"] for tool in assembled.tools] == ["imaginai_world"]
    assert "protocolo do mundo" in assembled.sift_prompt
