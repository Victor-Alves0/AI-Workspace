"""Contratos declarativos dos sistemas de RPG suportados pelo Imaginai."""

from __future__ import annotations

from typing import Any

_DND5E: dict[str, Any] = {
    "key": "dnd5e",
    "name": "D&D 5e",
    "version": "5e",
    "inventory": {
        "weight": {"supported": True, "default_enabled": True, "unit": "lb"},
        # D&D usa 50 moedas por libra. Outros sistemas podem desativar peso ou
        # declarar sua própria unidade sem alterar o inventário da interface.
        "currency_weight": {"supported": True, "default_enabled": True},
        "currencies": [
            {"key": "cp", "label": "PC", "name": "Peças de cobre", "weight": 0.02},
            {"key": "sp", "label": "PP", "name": "Peças de prata", "weight": 0.02},
            {"key": "ep", "label": "PE", "name": "Peças de electrum", "weight": 0.02},
            {"key": "gp", "label": "PO", "name": "Peças de ouro", "weight": 0.02},
            {"key": "pp", "label": "PL", "name": "Peças de platina", "weight": 0.02},
        ],
        "equipment_slots": ["armor", "main_hand", "off_hand", "attuned"],
    },
    "sheet": {
        "summary": [
            {"key": "armor_class", "label": "CA"},
            {"key": "initiative", "label": "Iniciativa"},
            {"key": "speed", "label": "Deslocamento"},
            {"key": "proficiency_bonus", "label": "Proficiência"},
            {"key": "passive_perception", "label": "Percepção passiva"},
        ],
        "attributes": [
            {
                "key": "strength",
                "label": "Força",
                "short": "FOR",
                "skills": ["athletics"],
            },
            {
                "key": "dexterity",
                "label": "Destreza",
                "short": "DES",
                "skills": ["acrobatics", "sleight_of_hand", "stealth"],
            },
            {
                "key": "constitution",
                "label": "Constituição",
                "short": "CON",
                "skills": [],
            },
            {
                "key": "intelligence",
                "label": "Inteligência",
                "short": "INT",
                "skills": ["arcana", "history", "investigation", "nature", "religion"],
            },
            {
                "key": "wisdom",
                "label": "Sabedoria",
                "short": "SAB",
                "skills": [
                    "animal_handling",
                    "insight",
                    "medicine",
                    "perception",
                    "survival",
                ],
            },
            {
                "key": "charisma",
                "label": "Carisma",
                "short": "CAR",
                "skills": ["deception", "intimidation", "performance", "persuasion"],
            },
        ],
        "skills": {
            "acrobatics": "Acrobacia",
            "animal_handling": "Adestrar Animais",
            "arcana": "Arcanismo",
            "athletics": "Atletismo",
            "deception": "Enganação",
            "history": "História",
            "insight": "Intuição",
            "intimidation": "Intimidação",
            "investigation": "Investigação",
            "medicine": "Medicina",
            "nature": "Natureza",
            "perception": "Percepção",
            "performance": "Atuação",
            "persuasion": "Persuasão",
            "religion": "Religião",
            "sleight_of_hand": "Prestidigitação",
            "stealth": "Furtividade",
            "survival": "Sobrevivência",
        },
    },
}

_SYSTEMS = {"dnd5e": _DND5E}


def system_definition(key: str) -> dict[str, Any]:
    try:
        return _SYSTEMS[key]
    except KeyError as exc:
        raise ValueError(f"Sistema de regras não suportado: {key}") from exc
