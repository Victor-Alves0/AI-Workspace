"""Magias da ficha: um formato só, venha de onde vier.

Três autores escrevem magias — a criação do personagem (sessão zero), o narrador
durante o jogo (aprendeu uma magia, detalhou uma já conhecida) e o próprio jogador
no painel. Todos passam por `normalize()`, que também monta o `effect` que o kernel
de regras lê ao conjurar (rules._cast_spell): ataque mágico com dano, ou cura.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MAX_SPELLS = 60
_DICE = re.compile(r"^(\d+d\d+|\d+)([+-]\d+)?$")


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def key_of(name: str) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:120] or "magia"


def _components(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [_text(c, 200) for c in value[:6] if _text(c, 200)]


def _dice(value: Any) -> str:
    dice = str(value or "").replace(" ", "").casefold()
    return dice if _DICE.fullmatch(dice) else ""


def normalize(record: Any, attack_modifier: int | None = None) -> dict[str, Any] | None:
    """Um registro de magia completo e seguro, ou None se não tiver nome."""
    if isinstance(record, str):
        record = {"name": record}
    if not isinstance(record, dict):
        return None
    name = _text(record.get("name"), 120)
    if not name:
        return None
    try:
        level = max(0, min(9, int(record.get("level", 0) or 0)))
    except (TypeError, ValueError):
        level = 0
    damage = _dice(record.get("damage"))
    try:
        healing = max(0, min(999, int(record.get("healing") or 0)))
    except (TypeError, ValueError):
        healing = 0
    # "ataque mágico" é o padrão para magia com dano, salvo se ela pedir salvaguarda
    save = _text(record.get("save"), 20)
    attack = bool(record.get("attack", bool(damage) and not save))
    spell: dict[str, Any] = {
        "key": key_of(name),
        "name": name,
        "level": level,
        "school": _text(record.get("school"), 80),
        "casting_time": _text(record.get("casting_time"), 80),
        "range": _text(record.get("range"), 80),
        "duration": _text(record.get("duration"), 80),
        "components": _components(record.get("components")),
        "concentration": bool(record.get("concentration")),
        "ritual": bool(record.get("ritual")),
        "description": str(record.get("description") or "").strip()[:4000],
        "prepared": record.get("prepared", True) is not False,
        "damage": damage,
        "damage_type": _text(record.get("damage_type"), 40),
        "attack": attack,
        "save": save,
        "healing": healing,
    }
    effect: dict[str, Any] = {}
    if damage and attack:
        effect = {"roll_kind": "spell_attack", "damage": damage, "damage_type": spell["damage_type"]}
        if attack_modifier is not None:
            effect["attack_modifier"] = attack_modifier
    elif healing:
        effect = {"healing": healing}
    if effect:
        spell["effect"] = effect
    return spell


def merge(current: Any, incoming: list[Any], attack_modifier: int | None, *,
          remove: list[Any] | None = None) -> list[dict[str, Any]]:
    """Adiciona/atualiza por nome (o que vier substitui o registro inteiro) e remove."""
    existing = [s for s in (normalize(r, attack_modifier) for r in (current or [])) if s] \
        if isinstance(current, list) else []
    by_key = {s["key"]: s for s in existing}
    order = [s["key"] for s in existing]
    for raw in incoming[:MAX_SPELLS]:
        spell = normalize(raw, attack_modifier)
        if spell is None:
            continue
        if spell["key"] not in by_key:
            order.append(spell["key"])
        by_key[spell["key"]] = spell
    for raw in remove or []:
        by_key.pop(key_of(_text(raw, 120)), None)
    return [by_key[k] for k in order if k in by_key][:MAX_SPELLS]
