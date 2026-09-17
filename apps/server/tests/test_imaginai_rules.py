"""Invariantes do World Kernel: intenção do jogador nunca cria realidade."""

from __future__ import annotations

import pytest

from aiworkspace.chat.orchestrator import (
    NativeToolOpts,
    _assemble_tools_and_prompt,
    _shape_tool_result,
)
from aiworkspace.imaginai.rules import ActionIntent, EntitySnapshot, ruleset_for
from aiworkspace.imaginai.systems import system_definition
from aiworkspace.schemas.imaginai import JournalCreate


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
        {"op": "decrement_spell_slot", "entity_id": "hero", "level": 3, "amount": 1},
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
        ActionIntent(
            action_type="cast_spell", actor=actor, parameters={"spell": "fireball"}
        )
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
