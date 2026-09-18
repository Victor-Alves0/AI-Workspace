"""Regras determinísticas do Imaginai.

Esta camada não narra e não consulta modelos. Ela recebe snapshots já autorizados
e decide se uma intenção é possível. O texto do jogador jamais entra como fato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    id: str
    kind: str
    key: str
    name: str
    location_id: str | None
    owner_entity_id: str | None
    state: dict[str, Any]
    active: bool = True


@dataclass(frozen=True, slots=True)
class ActionIntent:
    action_type: str
    actor: EntitySnapshot
    target: EntitySnapshot | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionDecision:
    status: str
    reason_code: str
    reason: str
    consumes_turn: bool = False
    tick_cost: int = 1
    mutations: tuple[dict[str, Any], ...] = ()
    event_type: str | None = None
    public_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        # `needs_adjudication` não é uma recusa: significa que a ficção e as
        # consequências precisam ser decididas antes de qualquer mutação.
        return self.status in {"allowed", "requires_check", "needs_adjudication"}

    @property
    def resolution(self) -> str:
        return {
            "allowed": "automatic",
            "requires_check": "check",
            "needs_adjudication": "narrative",
            "blocked": "contradiction",
        }.get(self.status, "narrative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "resolution": self.resolution,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "consumes_turn": self.consumes_turn,
            "tick_cost": self.tick_cost,
            "mutations": list(self.mutations),
            "event_type": self.event_type,
            "public_payload": self.public_payload,
        }


class Ruleset(Protocol):
    key: str

    def validate(self, intent: ActionIntent) -> ActionDecision: ...


def _blocked(code: str, reason: str) -> ActionDecision:
    return ActionDecision(status="blocked", reason_code=code, reason=reason)


def _system_state(entity: EntitySnapshot) -> dict[str, Any]:
    nested = entity.state.get("dnd5e")
    return nested if isinstance(nested, dict) else entity.state


def _same_place(actor: EntitySnapshot, target: EntitySnapshot) -> bool:
    return bool(actor.location_id and actor.location_id == target.location_id)


def _spell_record(actor: EntitySnapshot, requested: str) -> dict[str, Any] | None:
    """Busca a magia apenas na ficha autoritativa — nunca usa nível vindo do jogador."""
    spells = _system_state(actor).get("spells", [])
    wanted = requested.strip().casefold()
    if isinstance(spells, dict):
        for key, value in spells.items():
            record = value if isinstance(value, dict) else {}
            names = {str(key).casefold(), str(record.get("name", "")).casefold()}
            if wanted in names:
                return {"key": str(key), **record}
        return None
    if isinstance(spells, list):
        for raw in spells:
            if isinstance(raw, str) and raw.casefold() == wanted:
                return {"key": raw, "name": raw, "level": 0, "prepared": True}
            if not isinstance(raw, dict):
                continue
            names = {
                str(raw.get("key", "")).casefold(),
                str(raw.get("name", "")).casefold(),
            }
            if wanted in names:
                return raw
    return None


def _inventory_state(entity: EntitySnapshot) -> dict[str, Any]:
    inventory = entity.state.get("inventory")
    return inventory if isinstance(inventory, dict) else entity.state


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _attack_record(actor: EntitySnapshot, requested: str) -> dict[str, Any] | None:
    attacks = _system_state(actor).get("attacks", [])
    wanted = requested.strip().casefold()
    if not wanted:
        wanted = "unarmed"
    if isinstance(attacks, dict):
        attacks = [
            {"key": str(key), **(value if isinstance(value, dict) else {})}
            for key, value in attacks.items()
        ]
    if isinstance(attacks, list):
        for raw in attacks:
            if not isinstance(raw, dict):
                continue
            names = {
                str(raw.get("key", "")).casefold(),
                str(raw.get("name", "")).casefold(),
            }
            if wanted in names:
                return raw
    if wanted in {"unarmed", "desarmado", "ataque desarmado", "soco"}:
        attributes = _system_state(actor).get("attributes", {})
        attributes = attributes if isinstance(attributes, dict) else {}
        strength = attributes.get("strength", 10)
        if isinstance(strength, dict):
            strength = strength.get("score", 10)
        modifier = (_safe_int(strength, 10) - 10) // 2
        return {
            "key": "unarmed",
            "name": "Ataque desarmado",
            "attack_modifier": modifier
            + _safe_int(_system_state(actor).get("proficiency_bonus", 2), 2),
            "damage": f"1+{max(0, modifier)}",
            "damage_type": "bludgeoning",
        }
    return None


class Dnd5eRuleset:
    key = "dnd5e"

    def validate(self, intent: ActionIntent) -> ActionDecision:
        if not intent.actor.active:
            return _blocked("actor_inactive", "O personagem não pode agir neste momento.")

        handlers = {
            "take_item": self._take_item,
            "drop_item": self._drop_item,
            "equip_item": self._equip_item,
            "unequip_item": self._unequip_item,
            "use_item": self._use_item,
            "pay_currency": self._pay_currency,
            "interact": self._interact,
            "cast_spell": self._cast_spell,
            "examine": self._examine,
            "move": self._move,
            "travel": self._move,
            "attack": self._attack,
            "rest": self._rest,
            "wait": self._wait,
            "start_encounter": self._start_encounter,
        }
        handler = handlers.get(intent.action_type)
        if handler is None:
            # Criatividade não depende de termos um handler para cada verbo. O
            # adjudicador narrativo decide viabilidade, custo, teste e possíveis
            # consequências usando a ficção estabelecida; nada é mutado aqui.
            return ActionDecision(
                status="needs_adjudication",
                reason_code="creative_action",
                reason=(
                    "A intenção não contradiz uma regra conhecida. Adjudique-a pela "
                    "situação ficcional; peça um teste apenas se houver risco ou incerteza."
                ),
                consumes_turn=True,
            )
        return handler(intent)

    def _take_item(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind != "item":
            return _blocked(
                "target_not_accessible",
                "Não há nenhum objeto acessível correspondente neste local.",
            )
        if not target.active or not _same_place(intent.actor, target):
            return _blocked(
                "target_not_accessible",
                "Não há nenhum objeto acessível correspondente neste local.",
            )
        state = target.state
        discovered = {
            str(item) for item in _system_state(intent.actor).get("discovered_entity_ids", [])
        }
        if state.get("hidden") and target.id not in discovered:
            # Mensagem propositalmente não confirma que o objeto secreto existe.
            return _blocked(
                "target_not_accessible",
                "Não há nenhum objeto acessível correspondente neste local.",
            )
        if state.get("available", True) is False:
            return _blocked("target_unavailable", "Esse objeto não pode ser recolhido agora.")
        if target.owner_entity_id == intent.actor.id:
            return _blocked("already_owned", "O personagem já possui esse objeto.")
        if target.owner_entity_id:
            return _blocked("owned_by_other", "Esse objeto está sob a posse de outra criatura.")
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O objeto existe, está presente e pode ser recolhido.",
            consumes_turn=True,
            mutations=(
                {"op": "set_owner", "entity_id": target.id, "owner_entity_id": intent.actor.id},
                {"op": "set_location", "entity_id": target.id, "location_id": None},
            ),
            event_type="item_taken",
            public_payload={"item_id": target.id, "item_name": target.name},
        )

    def _drop_item(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind != "item" or target.owner_entity_id != intent.actor.id:
            return _blocked("item_not_owned", "O personagem não possui esse objeto.")
        if not intent.actor.location_id:
            return _blocked("location_unknown", "Não há um local válido para deixar o objeto.")
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O objeto pertence ao personagem e pode ser deixado aqui.",
            consumes_turn=True,
            mutations=(
                {"op": "set_owner", "entity_id": target.id, "owner_entity_id": None},
                {
                    "op": "set_location",
                    "entity_id": target.id,
                    "location_id": intent.actor.location_id,
                },
                {"op": "set_item_equipped", "entity_id": target.id, "equipped": False},
            ),
            event_type="item_dropped",
            public_payload={"item_id": target.id, "item_name": target.name},
        )

    def _equip_item(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind != "item" or target.owner_entity_id != intent.actor.id:
            return _blocked("item_not_owned", "O personagem não possui esse objeto.")
        inventory = _inventory_state(target)
        requested_slot = str(intent.parameters.get("slot") or "").strip()
        fixed_slot = str(inventory.get("slot") or "").strip()
        allowed_item_slots = inventory.get("allowed_slots", [])
        allowed_item_slots = (
            {str(value) for value in allowed_item_slots}
            if isinstance(allowed_item_slots, list)
            else set()
        )
        slot = requested_slot or fixed_slot
        allowed_slots = {"armor", "main_hand", "off_hand", "attuned"}
        wrong_item_slot = bool(
            requested_slot
            and (
                (allowed_item_slots and requested_slot not in allowed_item_slots)
                or (fixed_slot and not allowed_item_slots and requested_slot != fixed_slot)
            )
        )
        if (
            inventory.get("equippable", bool(slot)) is False
            or slot not in allowed_slots
            or wrong_item_slot
        ):
            return _blocked(
                "item_not_equippable", "Esse objeto não pode ser equipado nesse espaço."
            )
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O objeto pertence ao personagem e é compatível com o espaço.",
            consumes_turn=True,
            mutations=(
                {
                    "op": "set_item_equipped",
                    "entity_id": target.id,
                    "equipped": True,
                    "slot": slot,
                },
            ),
            event_type="item_equipped",
            public_payload={"item_id": target.id, "item_name": target.name, "slot": slot},
        )

    def _unequip_item(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind != "item" or target.owner_entity_id != intent.actor.id:
            return _blocked("item_not_owned", "O personagem não possui esse objeto.")
        if not _inventory_state(target).get("equipped"):
            return _blocked("item_not_equipped", "Esse objeto não está equipado.")
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O objeto equipado pode ser guardado.",
            consumes_turn=True,
            mutations=({"op": "set_item_equipped", "entity_id": target.id, "equipped": False},),
            event_type="item_unequipped",
            public_payload={"item_id": target.id, "item_name": target.name},
        )

    def _use_item(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind != "item" or target.owner_entity_id != intent.actor.id:
            return _blocked("item_not_owned", "O personagem não possui esse objeto.")
        inventory = _inventory_state(target)
        quantity = max(0, _safe_int(inventory.get("quantity", 1), 1))
        if quantity <= 0:
            return _blocked("item_depleted", "Esse objeto não possui usos restantes.")
        effect = inventory.get("effect")
        if not isinstance(effect, dict):
            return ActionDecision(
                status="needs_adjudication",
                reason_code="item_effect_adjudication",
                reason="O uso é possível, mas seu efeito depende da situação ficcional.",
                consumes_turn=True,
                event_type="item_used",
                public_payload={"item_id": target.id, "item_name": target.name},
            )
        mutations: list[dict[str, Any]] = []
        healing = max(0, _safe_int(effect.get("healing"), 0))
        if healing:
            mutations.append({"op": "adjust_hp", "entity_id": intent.actor.id, "amount": healing})
        if inventory.get("consumable", False):
            mutations.append({"op": "adjust_item_quantity", "entity_id": target.id, "amount": -1})
        if not mutations:
            return ActionDecision(
                status="needs_adjudication",
                reason_code="item_effect_adjudication",
                reason="O efeito estruturado não altera um recurso mecânico conhecido.",
                consumes_turn=True,
                event_type="item_used",
                public_payload={"item_id": target.id, "item_name": target.name},
            )
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O objeto possui um efeito mecânico autoritativo.",
            consumes_turn=True,
            mutations=tuple(mutations),
            event_type="item_used",
            public_payload={"item_id": target.id, "item_name": target.name},
        )

    def _pay_currency(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or not target.active or not _same_place(intent.actor, target):
            return _blocked("target_not_present", "O destinatário não está presente.")
        currency = str(intent.parameters.get("currency") or "gp").strip().lower()
        if currency not in {"cp", "sp", "ep", "gp", "pp"}:
            return _blocked("currency_invalid", "A moeda informada não pertence a este sistema.")
        amount = _safe_int(intent.parameters.get("amount"), 0)
        if amount <= 0:
            return _blocked("amount_invalid", "Informe uma quantidade positiva de moedas.")
        balance = _safe_int(_system_state(intent.actor).get("currencies", {}).get(currency), 0)
        if balance < amount:
            return _blocked("insufficient_funds", "O personagem não possui moedas suficientes.")
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O saldo e o destinatário foram validados.",
            consumes_turn=True,
            mutations=(
                {
                    "op": "adjust_currency",
                    "entity_id": intent.actor.id,
                    "currency": currency,
                    "amount": -amount,
                },
                {
                    "op": "adjust_currency",
                    "entity_id": target.id,
                    "currency": currency,
                    "amount": amount,
                },
            ),
            event_type="currency_paid",
            public_payload={"recipient_id": target.id, "currency": currency, "amount": amount},
        )

    def _interact(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind not in {"npc", "character", "creature"}:
            return _blocked("target_not_present", "Essa pessoa ou criatura não está presente.")
        if not target.active or not _same_place(intent.actor, target):
            return _blocked("target_not_present", "Essa pessoa ou criatura não está presente.")
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O alvo está presente e pode ser abordado.",
            event_type="interaction_started",
            public_payload={"target_id": target.id, "target_name": target.name},
        )

    def _cast_spell(self, intent: ActionIntent) -> ActionDecision:
        requested = str(intent.parameters.get("spell", "")).strip()
        if not requested:
            return _blocked("spell_required", "Informe qual magia o personagem tenta usar.")
        spell = _spell_record(intent.actor, requested)
        if spell is None or spell.get("known", True) is False:
            return _blocked("spell_not_known", "O personagem não conhece essa magia.")
        if spell.get("prepared", True) is False:
            return _blocked("spell_not_prepared", "Essa magia não está preparada.")
        if intent.parameters.get("_target_requested") and (
            intent.target is None
            or not intent.target.active
            or not _same_place(intent.actor, intent.target)
        ):
            return _blocked(
                "target_not_present",
                "Não há nenhum alvo correspondente ao alcance nesta cena.",
            )

        level = int(spell.get("level", 0) or 0)
        mutations: list[dict[str, Any]] = []
        if level > 0:
            slots = _system_state(intent.actor).get("spell_slots", {})
            slot = slots.get(str(level), slots.get(level)) if isinstance(slots, dict) else None
            current = int(slot.get("current", 0) or 0) if isinstance(slot, dict) else 0
            if current <= 0:
                return _blocked(
                    "spell_slot_unavailable",
                    f"O personagem não possui espaços de magia de {level}º nível disponíveis.",
                )
            mutations.append(
                {
                    "op": "decrement_spell_slot",
                    "entity_id": intent.actor.id,
                    "level": level,
                    "amount": 1,
                    "when": "always",
                }
            )

        effect = spell.get("effect")
        effect = effect if isinstance(effect, dict) else {}
        roll_kind = str(effect.get("roll_kind") or "").strip().lower()
        if roll_kind == "spell_attack":
            target = intent.target
            if target is None:
                return _blocked("target_required", "Essa magia exige um alvo presente.")
            hp = _system_state(target).get("hp", {})
            if not isinstance(hp, dict) or "max" not in hp:
                return ActionDecision(
                    status="needs_adjudication",
                    reason_code="target_mechanics_missing",
                    reason="A magia é válida, mas o alvo ainda não possui estatísticas de combate.",
                    consumes_turn=True,
                    mutations=tuple(mutations),
                    event_type="spell_adjudicated",
                    public_payload={
                        "spell_key": str(spell.get("key", requested)),
                        "spell_name": str(spell.get("name", requested)),
                        "level": level,
                    },
                )
            actor_state = _system_state(intent.actor)
            attack_modifier = _safe_int(
                effect.get("attack_modifier", actor_state.get("spell_attack_modifier")), 0
            )
            return ActionDecision(
                status="requires_check",
                reason_code="spell_attack_roll_required",
                reason="A magia e o alvo são válidos; resolva o ataque mágico no servidor.",
                consumes_turn=True,
                mutations=tuple(mutations),
                event_type="spell_attack_resolved",
                public_payload={
                    "spell_key": str(spell.get("key", requested)),
                    "spell_name": str(spell.get("name", requested)),
                    "level": level,
                    "roll_kind": "spell_attack",
                    "attack_modifier": attack_modifier,
                    "target_ac": max(1, _safe_int(_system_state(target).get("armor_class"), 10)),
                    "damage": str(effect.get("damage") or "1"),
                    "damage_type": str(effect.get("damage_type") or ""),
                },
            )

        healing = max(0, _safe_int(effect.get("healing"), 0))
        if healing:
            recipient = intent.target or intent.actor
            recipient_hp = _system_state(recipient).get("hp", {})
            if not isinstance(recipient_hp, dict) or "max" not in recipient_hp:
                return ActionDecision(
                    status="needs_adjudication",
                    reason_code="target_mechanics_missing",
                    reason="A magia é válida, mas o alvo não possui pontos de vida estruturados.",
                    consumes_turn=True,
                    mutations=tuple(mutations),
                    event_type="spell_adjudicated",
                    public_payload={
                        "spell_key": str(spell.get("key", requested)),
                        "spell_name": str(spell.get("name", requested)),
                        "level": level,
                    },
                )
            mutations.append({"op": "adjust_hp", "entity_id": recipient.id, "amount": healing})

        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="A magia pertence à ficha e seus recursos estão disponíveis.",
            consumes_turn=True,
            mutations=tuple(mutations),
            event_type="spell_cast",
            public_payload={
                "spell_key": str(spell.get("key", requested)),
                "spell_name": str(spell.get("name", requested)),
                "level": level,
            },
        )

    def _examine(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or not target.active or not _same_place(intent.actor, target):
            return _blocked("target_not_present", "Não há nada correspondente ao alcance.")
        if not target.state.get("hidden") and not target.state.get("examination_check"):
            return ActionDecision(
                status="allowed",
                reason_code="ok",
                reason="O alvo está ao alcance e suas características aparentes podem ser observadas.",
                event_type="entity_examined",
                public_payload={"target_id": target.id},
            )
        return ActionDecision(
            status="requires_check",
            reason_code="check_required",
            reason=(
                "Há informação realmente oculta ou um obstáculo relevante; resolva um "
                "teste e faça a falha mover a cena adiante com custo ou complicação."
            ),
            consumes_turn=True,
            event_type="examination_attempted",
            public_payload={"target_id": target.id},
        )

    def _move(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if target is None or target.kind != "location" or not target.active:
            return _blocked("destination_unknown", "Esse destino não está disponível.")
        if target.id == intent.actor.location_id:
            return _blocked("already_there", "O personagem já está nesse local.")
        discovered = {
            str(item) for item in _system_state(intent.actor).get("discovered_entity_ids", [])
        }
        if (
            target.state.get("hidden")
            and not target.state.get("discovered")
            and target.id not in discovered
        ):
            return _blocked("destination_unknown", "Esse destino não está disponível.")
        accessible_from = target.state.get("accessible_from", [])
        known_route = target.state.get("open_access") is True or (
            isinstance(accessible_from, list) and intent.actor.location_id in accessible_from
        )
        mutation = {
            "op": "set_location",
            "entity_id": intent.actor.id,
            "location_id": target.id,
        }
        if not known_route:
            return ActionDecision(
                status="needs_adjudication",
                reason_code="route_adjudication",
                reason="O destino é conhecido, mas a rota e seus riscos dependem da ficção.",
                consumes_turn=True,
                mutations=(mutation,),
                event_type="travel_resolved",
                public_payload={"destination_id": target.id, "destination_name": target.name},
            )
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="Há uma rota estabelecida e acessível até o destino.",
            consumes_turn=True,
            mutations=(mutation,),
            event_type="entity_moved",
            public_payload={"destination_id": target.id, "destination_name": target.name},
        )

    def _attack(self, intent: ActionIntent) -> ActionDecision:
        target = intent.target
        if (
            target is None
            or target.kind not in {"npc", "character", "creature"}
            or not target.active
            or not _same_place(intent.actor, target)
        ):
            return _blocked("target_not_present", "Não há um alvo correspondente ao alcance.")
        if target.id == intent.actor.id:
            return _blocked(
                "invalid_target", "O personagem não pode ser o próprio alvo desta ação."
            )
        hp = _system_state(target).get("hp", {})
        if not isinstance(hp, dict) or "max" not in hp:
            return ActionDecision(
                status="needs_adjudication",
                reason_code="target_mechanics_missing",
                reason="O confronto é possível, mas o alvo ainda não possui estatísticas de combate.",
                consumes_turn=True,
                event_type="attack_adjudicated",
                public_payload={"target_id": target.id},
            )
        if _safe_int(hp.get("current"), 1) <= 0:
            return _blocked("target_incapacitated", "O alvo já está incapacitado.")
        requested = str(intent.parameters.get("attack") or intent.parameters.get("weapon") or "")
        attack = _attack_record(intent.actor, requested)
        if attack is None:
            return _blocked("attack_unavailable", "O personagem não possui esse ataque disponível.")
        target_ac = max(1, _safe_int(_system_state(target).get("armor_class"), 10))
        return ActionDecision(
            status="requires_check",
            reason_code="attack_roll_required",
            reason="O ataque e o alvo são válidos; resolva a jogada de ataque no servidor.",
            consumes_turn=True,
            event_type="attack_resolved",
            public_payload={
                "target_id": target.id,
                "roll_kind": "attack",
                "attack_key": str(attack.get("key", requested or "unarmed")),
                "attack_name": str(attack.get("name", requested or "Ataque desarmado")),
                "attack_modifier": _safe_int(attack.get("attack_modifier"), 0),
                "target_ac": target_ac,
                "damage": str(attack.get("damage") or "1"),
                "damage_type": str(attack.get("damage_type") or ""),
            },
        )

    def _rest(self, intent: ActionIntent) -> ActionDecision:
        kind = str(intent.parameters.get("kind") or "short").strip().lower()
        if kind not in {"short", "long"}:
            return _blocked("rest_kind_invalid", "Use descanso curto ou longo.")
        if _system_state(intent.actor).get("rest_blocked"):
            return _blocked("rest_unavailable", "As condições atuais não permitem descansar.")
        mutations: tuple[dict[str, Any], ...] = ()
        tick_cost = 60
        if kind == "long":
            mutations = ({"op": "restore_long_rest", "entity_id": intent.actor.id},)
            tick_cost = 480
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="As condições permitem o descanso solicitado.",
            consumes_turn=True,
            tick_cost=tick_cost,
            mutations=mutations,
            event_type="rest_completed",
            public_payload={"kind": kind, "minutes": tick_cost},
        )

    def _start_encounter(self, intent: ActionIntent) -> ActionDecision:
        """Inimigos atacam sem que o jogador tenha começado (emboscada, guarda que saca a
        espada). Quem entra e quem age primeiro é decidido pelo servidor, na iniciativa."""
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O confronto começa; a iniciativa define a ordem dos turnos.",
            consumes_turn=False,
            event_type="encounter_requested",
            public_payload={},
        )

    def _wait(self, intent: ActionIntent) -> ActionDecision:
        minutes = max(1, min(_safe_int(intent.parameters.get("minutes"), 1), 1440))
        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="O tempo pode avançar sem alterar fatos não estabelecidos.",
            consumes_turn=True,
            tick_cost=minutes,
            event_type="time_advanced",
            public_payload={"minutes": minutes},
        )


_RULESETS: dict[str, Ruleset] = {"dnd5e": Dnd5eRuleset()}


def ruleset_for(key: str) -> Ruleset:
    try:
        return _RULESETS[key]
    except KeyError as exc:
        raise ValueError(f"Sistema de regras não suportado: {key}") from exc
