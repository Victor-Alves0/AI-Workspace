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


class Dnd5eRuleset:
    key = "dnd5e"

    def validate(self, intent: ActionIntent) -> ActionDecision:
        if not intent.actor.active:
            return _blocked("actor_inactive", "O personagem não pode agir neste momento.")

        handlers = {
            "take_item": self._take_item,
            "interact": self._interact,
            "cast_spell": self._cast_spell,
            "examine": self._examine,
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
            intent.target is None or not intent.target.active or not _same_place(intent.actor, intent.target)
        ):
            return _blocked(
                "target_not_present",
                "Não há nenhum alvo correspondente ao alcance nesta cena.",
            )

        level = int(spell.get("level", 0) or 0)
        mutations: tuple[dict[str, Any], ...] = ()
        if level > 0:
            slots = _system_state(intent.actor).get("spell_slots", {})
            slot = slots.get(str(level), slots.get(level)) if isinstance(slots, dict) else None
            current = int(slot.get("current", 0) or 0) if isinstance(slot, dict) else 0
            if current <= 0:
                return _blocked(
                    "spell_slot_unavailable",
                    f"O personagem não possui espaços de magia de {level}º nível disponíveis.",
                )
            mutations = (
                {
                    "op": "decrement_spell_slot",
                    "entity_id": intent.actor.id,
                    "level": level,
                    "amount": 1,
                },
            )

        return ActionDecision(
            status="allowed",
            reason_code="ok",
            reason="A magia pertence à ficha e seus recursos estão disponíveis.",
            consumes_turn=True,
            mutations=mutations,
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


_RULESETS: dict[str, Ruleset] = {"dnd5e": Dnd5eRuleset()}


def ruleset_for(key: str) -> Ruleset:
    try:
        return _RULESETS[key]
    except KeyError as exc:
        raise ValueError(f"Sistema de regras não suportado: {key}") from exc
