"""Criação de personagem D&D 5e — regras puras (sem banco), testadas com dado injetável.

Antes, a ficha nascia com tudo 10, sem salvaguardas nem perícias proficientes, e o
narrador "registrava" classe e atributos só no texto. Aqui a classe, a raça e o
antecedente viram números pelo livro (PHB): dado de vida, salvaguardas, perícias,
CA pela armadura inicial, ataques das armas, conjuração e equipamento. O jogador
escolhe; o servidor calcula e rola.

Fluxo (acumulativo — cada chamada completa o que faltava):
    método de atributos  → roll (4d6, descarta o menor, rolado AQUI) | standard_array
                           | point_buy (27 pontos) | manual (valores livres 3–18)
    distribuição         → qual valor vai em qual atributo
    classe, raça, antecedente, perícias da classe (e as do antecedente, se inventado)
    → `derive()` devolve a ficha calculada + a lista do que ainda falta escolher.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from .combat import Roller, secure_roller

ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
ABILITY_PT = {
    "strength": "FOR", "dexterity": "DES", "constitution": "CON",
    "intelligence": "INT", "wisdom": "SAB", "charisma": "CAR",
}
_ABILITY_ALIASES = {
    "for": "strength", "forca": "strength", "str": "strength",
    "des": "dexterity", "destreza": "dexterity", "dex": "dexterity",
    "con": "constitution", "constituicao": "constitution",
    "int": "intelligence", "inteligencia": "intelligence",
    "sab": "wisdom", "sabedoria": "wisdom", "wis": "wisdom",
    "car": "charisma", "carisma": "charisma", "cha": "charisma",
}

SKILLS = (
    "acrobatics", "animal_handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight_of_hand", "stealth", "survival",
)
_SKILL_ALIASES = {
    "acrobacia": "acrobatics", "adestrar animais": "animal_handling", "lidar com animais": "animal_handling",
    "arcanismo": "arcana", "atletismo": "athletics", "enganacao": "deception",
    "historia": "history", "intuicao": "insight", "intimidacao": "intimidation",
    "investigacao": "investigation", "medicina": "medicine", "natureza": "nature",
    "percepcao": "perception", "atuacao": "performance", "persuasao": "persuasion",
    "religiao": "religion", "prestidigitacao": "sleight_of_hand", "furtividade": "stealth",
    "sobrevivencia": "survival",
}

STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
POINT_BUY_BUDGET = 27
POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
METHODS = ("roll", "standard_array", "point_buy", "manual")


class BuildError(ValueError):
    """Escolha inválida — a mensagem volta ao narrador/jogador."""


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return " ".join(text.casefold().replace("_", " ").replace("-", " ").split())


# --------------------------------------------------------------------------- #
# Equipamento (peso em libras, como no PHB)                                    #
# --------------------------------------------------------------------------- #
def _weapon(name: str, damage: str, dtype: str, weight: float, *props: str) -> dict[str, Any]:
    return {"name": name, "kind": "weapon", "damage": damage, "damage_type": dtype,
            "weight": weight, "properties": list(props), "quantity": 1}


def _gear(name: str, weight: float, quantity: int = 1) -> dict[str, Any]:
    return {"name": name, "kind": "gear", "weight": weight, "quantity": quantity}


_ARMOR = {
    "leather": {"name": "Armadura de couro", "base": 11, "dex_cap": None, "weight": 10},
    "scale": {"name": "Brunea", "base": 14, "dex_cap": 2, "weight": 45},
    "chain": {"name": "Cota de malha", "base": 16, "dex_cap": 0, "weight": 55},
}
_SHIELD = {"name": "Escudo", "kind": "shield", "weight": 6, "quantity": 1}

_PACKS = {
    "explorer": _gear("Pacote de aventureiro", 59),
    "dungeoneer": _gear("Pacote de explorador de masmorras", 61.5),
    "diplomat": _gear("Pacote de diplomata", 36),
    "priest": _gear("Pacote de sacerdote", 24),
    "scholar": _gear("Pacote de estudioso", 10),
    "burglar": _gear("Pacote de assaltante", 44.5),
}

DAGGER = _weapon("Adaga", "1d4", "perfurante", 1, "finesse", "light", "thrown")
LIGHT_CROSSBOW = _weapon("Besta leve", "1d8", "perfurante", 5, "ranged")

# --------------------------------------------------------------------------- #
# Classes                                                                      #
# --------------------------------------------------------------------------- #
CLASSES: dict[str, dict[str, Any]] = {
    "barbarian": {
        "name": "Bárbaro", "aliases": ["barbaro", "barbarian"], "hit_die": 12,
        "saves": ["strength", "constitution"],
        "skills": (2, ["animal_handling", "athletics", "intimidation", "nature", "perception", "survival"]),
        "armor": None, "shield": False, "unarmored": "constitution",
        "weapons": [_weapon("Machado grande", "1d12", "cortante", 7, "heavy", "two_handed"),
                    {**_weapon("Machadinha", "1d6", "cortante", 2, "light", "thrown"), "quantity": 2}],
        "gear": [_PACKS["explorer"], {**_weapon("Azagaia", "1d6", "perfurante", 2, "thrown"), "quantity": 4}],
        "gold": 10,
    },
    "bard": {
        "name": "Bardo", "aliases": ["bardo", "bard"], "hit_die": 8,
        "saves": ["dexterity", "charisma"], "skills": (3, list(SKILLS)),
        "armor": "leather", "shield": False,
        "weapons": [_weapon("Rapieira", "1d8", "perfurante", 2, "finesse"), DAGGER],
        "gear": [_PACKS["diplomat"], _gear("Alaúde", 2)],
        "caster": {"ability": "charisma", "type": "full", "cantrips": 2, "spells": 4},
        "gold": 10,
    },
    "warlock": {
        "name": "Bruxo", "aliases": ["bruxo", "warlock"], "hit_die": 8,
        "saves": ["wisdom", "charisma"],
        "skills": (2, ["arcana", "deception", "history", "intimidation", "investigation", "nature", "religion"]),
        "armor": "leather", "shield": False,
        "weapons": [LIGHT_CROSSBOW, {**DAGGER, "quantity": 2}],
        "gear": [_gear("Virotes", 1.5, 20), _gear("Bolsa de componentes", 2), _PACKS["scholar"]],
        "caster": {"ability": "charisma", "type": "pact", "cantrips": 2, "spells": 2},
        "gold": 10,
    },
    "cleric": {
        "name": "Clérigo", "aliases": ["clerigo", "cleric", "sacerdote"], "hit_die": 8,
        "saves": ["wisdom", "charisma"],
        "skills": (2, ["history", "insight", "medicine", "persuasion", "religion"]),
        "armor": "scale", "shield": True,
        "weapons": [_weapon("Maça", "1d6", "concussão", 4), LIGHT_CROSSBOW],
        "gear": [_gear("Virotes", 1.5, 20), _gear("Símbolo sagrado", 1), _PACKS["priest"]],
        "caster": {"ability": "wisdom", "type": "full", "cantrips": 3, "prepared": True},
        "gold": 10,
    },
    "druid": {
        "name": "Druida", "aliases": ["druida", "druid"], "hit_die": 8,
        "saves": ["intelligence", "wisdom"],
        "skills": (2, ["arcana", "animal_handling", "insight", "medicine", "nature", "perception", "religion", "survival"]),
        "armor": "leather", "shield": True,
        "weapons": [_weapon("Cimitarra", "1d6", "cortante", 3, "finesse", "light")],
        "gear": [_gear("Foco druídico", 1), _PACKS["explorer"]],
        "caster": {"ability": "wisdom", "type": "full", "cantrips": 2, "prepared": True},
        "gold": 10,
    },
    "sorcerer": {
        "name": "Feiticeiro", "aliases": ["feiticeiro", "sorcerer"], "hit_die": 6,
        "saves": ["constitution", "charisma"],
        "skills": (2, ["arcana", "deception", "insight", "intimidation", "persuasion", "religion"]),
        "armor": None, "shield": False,
        "weapons": [LIGHT_CROSSBOW, {**DAGGER, "quantity": 2}],
        "gear": [_gear("Virotes", 1.5, 20), _gear("Bolsa de componentes", 2), _PACKS["dungeoneer"]],
        "caster": {"ability": "charisma", "type": "full", "cantrips": 4, "spells": 2},
        "gold": 10,
    },
    "fighter": {
        "name": "Guerreiro", "aliases": ["guerreiro", "fighter", "lutador"], "hit_die": 10,
        "saves": ["strength", "constitution"],
        "skills": (2, ["acrobatics", "animal_handling", "athletics", "history", "insight", "intimidation", "perception", "survival"]),
        "armor": "chain", "shield": True,
        "weapons": [_weapon("Espada longa", "1d8", "cortante", 3, "versatile"), LIGHT_CROSSBOW],
        "gear": [_gear("Virotes", 1.5, 20), _PACKS["dungeoneer"]],
        "gold": 10,
    },
    "rogue": {
        "name": "Ladino", "aliases": ["ladino", "rogue", "ladrao"], "hit_die": 8,
        "saves": ["dexterity", "intelligence"],
        "skills": (4, ["acrobatics", "athletics", "deception", "insight", "intimidation", "investigation",
                       "perception", "performance", "persuasion", "sleight_of_hand", "stealth"]),
        "armor": "leather", "shield": False, "expertise": 2,
        "weapons": [_weapon("Rapieira", "1d8", "perfurante", 2, "finesse"),
                    _weapon("Arco curto", "1d6", "perfurante", 2, "ranged"), {**DAGGER, "quantity": 2}],
        "gear": [_gear("Flechas", 1, 20), _gear("Ferramentas de ladrão", 1), _PACKS["burglar"]],
        "gold": 10,
    },
    "wizard": {
        "name": "Mago", "aliases": ["mago", "wizard"], "hit_die": 6,
        "saves": ["intelligence", "wisdom"],
        "skills": (2, ["arcana", "history", "insight", "investigation", "medicine", "religion"]),
        "armor": None, "shield": False,
        "weapons": [_weapon("Bordão", "1d6", "concussão", 4, "versatile")],
        "gear": [_gear("Bolsa de componentes", 2), _gear("Grimório", 3), _PACKS["scholar"]],
        "caster": {"ability": "intelligence", "type": "full", "cantrips": 3, "prepared": True},
        "gold": 10,
    },
    "monk": {
        "name": "Monge", "aliases": ["monge", "monk"], "hit_die": 8,
        "saves": ["strength", "dexterity"],
        "skills": (2, ["acrobatics", "athletics", "history", "insight", "religion", "stealth"]),
        "armor": None, "shield": False, "unarmored": "wisdom", "martial_arts": True,
        "weapons": [_weapon("Espada curta", "1d6", "perfurante", 2, "finesse", "light"),
                    {**_weapon("Dardo", "1d4", "perfurante", 0.25, "finesse", "thrown", "ranged"), "quantity": 10}],
        "gear": [_PACKS["explorer"]],
        "gold": 5,
    },
    "paladin": {
        "name": "Paladino", "aliases": ["paladino", "paladin"], "hit_die": 10,
        "saves": ["wisdom", "charisma"],
        "skills": (2, ["athletics", "insight", "intimidation", "medicine", "persuasion", "religion"]),
        "armor": "chain", "shield": True,
        "weapons": [_weapon("Espada longa", "1d8", "cortante", 3, "versatile"),
                    {**_weapon("Azagaia", "1d6", "perfurante", 2, "thrown"), "quantity": 5}],
        "gear": [_gear("Símbolo sagrado", 1), _PACKS["priest"]],
        "caster": {"ability": "charisma", "type": "half", "cantrips": 0, "prepared": True},
        "gold": 10,
    },
    "ranger": {
        "name": "Patrulheiro", "aliases": ["patrulheiro", "ranger", "cacador"], "hit_die": 10,
        "saves": ["strength", "dexterity"],
        "skills": (3, ["animal_handling", "athletics", "insight", "investigation", "nature", "perception", "stealth", "survival"]),
        "armor": "leather", "shield": False,
        "weapons": [{**_weapon("Espada curta", "1d6", "perfurante", 2, "finesse", "light"), "quantity": 2},
                    _weapon("Arco longo", "1d8", "perfurante", 2, "ranged", "heavy")],
        "gear": [_gear("Flechas", 1, 20), _PACKS["explorer"]],
        "caster": {"ability": "wisdom", "type": "half", "cantrips": 0, "spells": 0},
        "gold": 10,
    },
}

# --------------------------------------------------------------------------- #
# Raças (PHB). "flex" = escolha livre de +1 (Meio-Elfo).                       #
# --------------------------------------------------------------------------- #
RACES: dict[str, dict[str, Any]] = {
    "human": {"name": "Humano", "aliases": ["humano", "human", "humana"],
              "bonuses": dict.fromkeys(ABILITIES, 1), "speed": 30},
    "dwarf": {"name": "Anão", "aliases": ["anao", "dwarf", "ana"], "bonuses": {"constitution": 2}, "speed": 25},
    "hill_dwarf": {"name": "Anão da Colina", "aliases": ["anao da colina", "hill dwarf"],
                   "bonuses": {"constitution": 2, "wisdom": 1}, "speed": 25, "hp_per_level": 1},
    "mountain_dwarf": {"name": "Anão da Montanha", "aliases": ["anao da montanha", "mountain dwarf"],
                       "bonuses": {"constitution": 2, "strength": 2}, "speed": 25},
    "elf": {"name": "Elfo", "aliases": ["elfo", "elf", "elfa"], "bonuses": {"dexterity": 2}, "speed": 30,
            "skills": ["perception"]},
    "high_elf": {"name": "Alto Elfo", "aliases": ["alto elfo", "high elf"],
                 "bonuses": {"dexterity": 2, "intelligence": 1}, "speed": 30, "skills": ["perception"]},
    "wood_elf": {"name": "Elfo da Floresta", "aliases": ["elfo da floresta", "wood elf"],
                 "bonuses": {"dexterity": 2, "wisdom": 1}, "speed": 35, "skills": ["perception"]},
    "drow": {"name": "Drow", "aliases": ["drow", "elfo negro"], "bonuses": {"dexterity": 2, "charisma": 1},
             "speed": 30, "skills": ["perception"]},
    "halfling": {"name": "Halfling", "aliases": ["halfling", "pequenino"], "bonuses": {"dexterity": 2}, "speed": 25},
    "lightfoot": {"name": "Halfling Pés-Leves", "aliases": ["pes leves", "halfling pes leves", "lightfoot"],
                  "bonuses": {"dexterity": 2, "charisma": 1}, "speed": 25},
    "stout": {"name": "Halfling Robusto", "aliases": ["robusto", "halfling robusto", "stout"],
              "bonuses": {"dexterity": 2, "constitution": 1}, "speed": 25},
    "dragonborn": {"name": "Draconato", "aliases": ["draconato", "dragonborn"],
                   "bonuses": {"strength": 2, "charisma": 1}, "speed": 30},
    "gnome": {"name": "Gnomo", "aliases": ["gnomo", "gnome"], "bonuses": {"intelligence": 2}, "speed": 25},
    "half_elf": {"name": "Meio-Elfo", "aliases": ["meio elfo", "half elf"], "bonuses": {"charisma": 2},
                 "flex": 2, "speed": 30},
    "half_orc": {"name": "Meio-Orc", "aliases": ["meio orc", "half orc"],
                 "bonuses": {"strength": 2, "constitution": 1}, "speed": 30, "skills": ["intimidation"]},
    "tiefling": {"name": "Tiefling", "aliases": ["tiefling", "tiferino"],
                 "bonuses": {"charisma": 2, "intelligence": 1}, "speed": 30},
}

BACKGROUNDS: dict[str, dict[str, Any]] = {
    "acolyte": {"name": "Acólito", "aliases": ["acolito", "acolyte"], "skills": ["insight", "religion"], "gold": 15},
    "guild_artisan": {"name": "Artesão de Guilda", "aliases": ["artesao", "artesao de guilda", "guild artisan"],
                      "skills": ["insight", "persuasion"], "gold": 15},
    "entertainer": {"name": "Artista", "aliases": ["artista", "entertainer"], "skills": ["acrobatics", "performance"], "gold": 15},
    "charlatan": {"name": "Charlatão", "aliases": ["charlatao", "charlatan"], "skills": ["deception", "sleight_of_hand"], "gold": 15},
    "criminal": {"name": "Criminoso", "aliases": ["criminoso", "criminal"], "skills": ["deception", "stealth"], "gold": 15},
    "hermit": {"name": "Eremita", "aliases": ["eremita", "hermit"], "skills": ["medicine", "religion"], "gold": 5},
    "outlander": {"name": "Forasteiro", "aliases": ["forasteiro", "outlander"], "skills": ["athletics", "survival"], "gold": 10},
    "folk_hero": {"name": "Herói do Povo", "aliases": ["heroi do povo", "folk hero"], "skills": ["animal_handling", "survival"], "gold": 10},
    "sailor": {"name": "Marinheiro", "aliases": ["marinheiro", "sailor"], "skills": ["athletics", "perception"], "gold": 10},
    "noble": {"name": "Nobre", "aliases": ["nobre", "noble"], "skills": ["history", "persuasion"], "gold": 25},
    "urchin": {"name": "Órfão", "aliases": ["orfao", "urchin", "crianca de rua"], "skills": ["sleight_of_hand", "stealth"], "gold": 10},
    "sage": {"name": "Sábio", "aliases": ["sabio", "sage"], "skills": ["arcana", "history"], "gold": 10},
    "soldier": {"name": "Soldado", "aliases": ["soldado", "soldier"], "skills": ["athletics", "intimidation"], "gold": 10},
}

# espaços de magia do conjurador completo, por nível de personagem (PHB)
_FULL_SLOTS = [
    [2], [3], [4, 2], [4, 3], [4, 3, 2], [4, 3, 3], [4, 3, 3, 1], [4, 3, 3, 2], [4, 3, 3, 3, 1],
    [4, 3, 3, 3, 2], [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1, 1],
    [4, 3, 3, 3, 2, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1],
    [4, 3, 3, 3, 2, 1, 1, 1, 1], [4, 3, 3, 3, 3, 1, 1, 1, 1], [4, 3, 3, 3, 3, 2, 1, 1, 1],
    [4, 3, 3, 3, 3, 2, 2, 1, 1],
]


def _find(table: dict[str, dict[str, Any]], value: Any) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    wanted = norm(value)
    if not wanted:
        return None, None
    for key, entry in table.items():
        if wanted == norm(key) or wanted == norm(entry["name"]) or wanted in (norm(a) for a in entry["aliases"]):
            return key, entry
    return None, None


def find_class(value: Any):
    return _find(CLASSES, value)


def find_race(value: Any):
    return _find(RACES, value)


def find_background(value: Any):
    return _find(BACKGROUNDS, value)


def ability_key(value: Any) -> str | None:
    wanted = norm(value)
    if wanted in ABILITIES:
        return wanted
    return _ABILITY_ALIASES.get(wanted)


def skill_key(value: Any) -> str | None:
    wanted = norm(value).replace(" ", "_")
    if wanted in SKILLS:
        return wanted
    return _SKILL_ALIASES.get(norm(value))


def modifier(score: int) -> int:
    return (score - 10) // 2


def proficiency_bonus(level: int) -> int:
    return 2 + max(0, min(19, level - 1)) // 4


# --------------------------------------------------------------------------- #
# Atributos                                                                    #
# --------------------------------------------------------------------------- #
def roll_ability(roller: Roller = secure_roller) -> dict[str, Any]:
    """4d6, descarta o menor."""
    dice = [roller(6) for _ in range(4)]
    kept = sorted(dice, reverse=True)[:3]
    return {"total": sum(kept), "dice": dice}


def roll_pool(roller: Roller = secure_roller) -> list[dict[str, Any]]:
    return [roll_ability(roller) for _ in range(6)]


def parse_scores(raw: Any) -> dict[str, int]:
    """{"FOR": 15, "des": 14, ...} → {"strength": 15, ...} (chaves PT/EN aceitas)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        ability = ability_key(key)
        if ability is None:
            raise BuildError(f"Atributo desconhecido: {key}")
        try:
            out[ability] = int(value)
        except (TypeError, ValueError) as exc:
            raise BuildError(f"Valor inválido para {ABILITY_PT[ability]}: {value}") from exc
    return out


def validate_abilities(method: str, assignment: dict[str, int], pool: list[int] | None) -> dict[str, int]:
    """Confere a distribuição contra o método. Devolve os valores BASE (sem raça)."""
    if set(assignment) != set(ABILITIES):
        faltam = [ABILITY_PT[a] for a in ABILITIES if a not in assignment]
        raise BuildError(f"Distribua os seis atributos (faltam: {', '.join(faltam)}).")
    valores = sorted(assignment.values(), reverse=True)
    if method == "standard_array":
        if valores != sorted(STANDARD_ARRAY, reverse=True):
            raise BuildError("No conjunto padrão use exatamente 15, 14, 13, 12, 10 e 8 — cada um uma vez.")
    elif method == "roll":
        if not pool:
            raise BuildError("Role os atributos primeiro (roll_abilities).")
        if valores != sorted(pool, reverse=True):
            raise BuildError(
                f"Use exatamente os valores rolados ({', '.join(map(str, sorted(pool, reverse=True)))}), "
                "cada um uma vez."
            )
    elif method == "point_buy":
        fora = [ABILITY_PT[a] for a, v in assignment.items() if v not in POINT_BUY_COST]
        if fora:
            raise BuildError(f"Na compra de pontos cada atributo vai de 8 a 15 (fora: {', '.join(fora)}).")
        gasto = sum(POINT_BUY_COST[v] for v in assignment.values())
        if gasto > POINT_BUY_BUDGET:
            raise BuildError(f"A compra de pontos gastou {gasto} de {POINT_BUY_BUDGET}.")
    elif method == "manual":
        fora = [ABILITY_PT[a] for a, v in assignment.items() if not 3 <= v <= 18]
        if fora:
            raise BuildError(f"Valores manuais vão de 3 a 18 antes da raça (fora: {', '.join(fora)}).")
    else:
        raise BuildError(f"Método de atributos inválido. Use: {', '.join(METHODS)}.")
    return dict(assignment)


def racial_bonuses(race_key: str | None, race: dict[str, Any] | None, chosen: dict[str, int]) -> tuple[dict[str, int], str | None]:
    """Bônus da raça. Raça do livro: fixos (+ escolha livre do Meio-Elfo). Raça inventada
    pela campanha: regra flexível (Tasha) — +2/+1 ou +1/+1/+1, à escolha."""
    if race is not None:
        bonus = dict(race["bonuses"])
        flex = race.get("flex", 0)
        if flex:
            extras = [a for a, v in chosen.items() if v == 1 and a not in bonus]
            if len(extras) < flex:
                return bonus, f"escolha {flex} atributos diferentes de CAR para +1 (Meio-Elfo)"
            for a in extras[:flex]:
                bonus[a] = 1
        return bonus, None
    if not chosen:
        return {}, "bônus de atributo da ancestralidade (+2 e +1, ou +1 em três)"
    valores = sorted(chosen.values(), reverse=True)
    if valores not in ([2, 1], [1, 1, 1]):
        raise BuildError("Ancestralidade própria: distribua +2 e +1 (ou +1, +1, +1) em atributos diferentes.")
    return dict(chosen), None


# --------------------------------------------------------------------------- #
# Ficha derivada                                                               #
# --------------------------------------------------------------------------- #
def _hp_for_level(die: int, con_mod: int, level: int, method: str, rolls: list[int],
                  extra_per_level: int, roller: Roller) -> tuple[int, list[int]]:
    """Nível 1: dado cheio + CON. Demais níveis: média (die/2+1) ou rolagem, + CON."""
    total = die + con_mod + extra_per_level
    novos = list(rolls)
    for i in range(level - 1):
        if method == "roll":
            if i >= len(novos):
                novos.append(roller(die))
            ganho = novos[i]
        else:
            ganho = die // 2 + 1
        total += max(1, ganho + con_mod) + extra_per_level
    return max(1, total), novos


def _armor_class(cls: dict[str, Any], scores: dict[str, int]) -> tuple[int, str]:
    dex = modifier(scores["dexterity"])
    shield = 2 if cls.get("shield") else 0
    armor_key = cls.get("armor")
    if armor_key:
        armor = _ARMOR[armor_key]
        cap = armor["dex_cap"]
        dex_part = dex if cap is None else min(dex, cap)
        return armor["base"] + dex_part + shield, armor["name"] + (" + escudo" if shield else "")
    extra = cls.get("unarmored")
    if extra:
        return 10 + dex + modifier(scores[extra]) + shield, "Defesa sem armadura"
    return 10 + dex + shield, "Sem armadura"


def _attacks(cls: dict[str, Any], scores: dict[str, int], prof: int) -> list[dict[str, Any]]:
    str_mod, dex_mod = modifier(scores["strength"]), modifier(scores["dexterity"])
    out = []
    for weapon in cls["weapons"]:
        props = weapon.get("properties", [])
        if "ranged" in props:
            mod = dex_mod
        elif "finesse" in props or cls.get("martial_arts"):
            mod = max(str_mod, dex_mod)
        else:
            mod = str_mod
        dano = weapon["damage"] + (f"{mod:+d}" if mod else "")
        out.append({
            "key": norm(weapon["name"]).replace(" ", "_"), "name": weapon["name"],
            "attack_modifier": mod + prof, "damage": dano, "damage_type": weapon["damage_type"],
        })
    if cls.get("martial_arts"):
        mod = max(str_mod, dex_mod)
        out.append({"key": "unarmed", "name": "Artes marciais", "attack_modifier": mod + prof,
                    "damage": "1d4" + (f"{mod:+d}" if mod else ""), "damage_type": "concussão"})
    return out


def spell_slots(caster: dict[str, Any] | None, level: int) -> dict[str, dict[str, int]]:
    if not caster:
        return {}
    kind = caster["type"]
    if kind == "full":
        row = _FULL_SLOTS[level - 1]
    elif kind == "half":
        row = _FULL_SLOTS[(level + 1) // 2 - 1] if level >= 2 else []
    else:  # pact (Bruxo): poucos espaços, todos do mesmo nível
        n = 1 if level == 1 else 2 if level < 11 else 3 if level < 17 else 4
        nivel = min(5, (level + 1) // 2)
        return {str(nivel): {"current": n, "max": n}}
    return {str(i + 1): {"current": n, "max": n} for i, n in enumerate(row)}


def _spell_limits(caster: dict[str, Any], scores: dict[str, int], level: int) -> tuple[int, int]:
    cantrips = caster.get("cantrips", 0)
    if caster.get("prepared"):
        spells = max(1, modifier(scores[caster["ability"]]) + (level if caster["type"] == "full" else level // 2))
        if caster["type"] == "half" and level < 2:
            spells = 0
    else:
        spells = caster.get("spells", 0)
    return cantrips, spells


def _spells(raw: Any, caster: dict[str, Any] | None, scores: dict[str, int], level: int, prof: int) -> list[dict[str, Any]]:
    if not caster or not isinstance(raw, list):
        return []
    mod = modifier(scores[caster["ability"]])
    out = []
    for item in raw[:40]:
        record = item if isinstance(item, dict) else {"name": item}
        nome = " ".join(str(record.get("name") or "").split())[:80]
        if not nome:
            continue
        try:
            nivel = max(0, min(9, int(record.get("level", 0) or 0)))
        except (TypeError, ValueError):
            nivel = 0
        spell: dict[str, Any] = {"key": norm(nome).replace(" ", "_"), "name": nome, "level": nivel, "prepared": True}
        dano = str(record.get("damage") or "").replace(" ", "")
        if dano:
            spell["effect"] = {"attack_modifier": mod + prof, "damage": dano}
        out.append(spell)
    cantrips, spells = _spell_limits(caster, scores, level)
    if level == 1:
        n_c = sum(1 for s in out if s["level"] == 0)
        n_s = sum(1 for s in out if s["level"] > 0)
        if n_c > cantrips or n_s > spells:
            raise BuildError(
                f"Magias demais para o nível 1: até {cantrips} truque(s) e {spells} magia(s) de 1º nível."
            )
    return out


def derive(build: dict[str, Any], roller: Roller = secure_roller) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Calcula a ficha a partir das escolhas acumuladas em `build`.

    Devolve (campos dnd5e calculados, o que falta escolher, build atualizado com rolagens
    novas). Campos só aparecem quando dá para calculá-los — a ficha vai se completando."""
    build = dict(build)
    missing: list[str] = []
    sheet: dict[str, Any] = {}

    _, cls = find_class(build.get("class"))
    if cls is None:
        missing.append("class (uma das classes do livro)")
    else:
        sheet["class"] = cls["name"]
    race_key, race = find_race(build.get("race"))
    race_name = " ".join(str(build.get("race") or "").split())[:120]
    if not race_name:
        missing.append("race/ancestralidade")
    else:
        sheet["ancestry"] = race["name"] if race else race_name
    _, bg = find_background(build.get("background"))
    bg_name = " ".join(str(build.get("background") or "").split())[:120]
    if not bg_name:
        missing.append("background/antecedente")
    else:
        sheet["background"] = bg["name"] if bg else bg_name

    try:
        level = max(1, min(20, int(build.get("level") or 1)))
    except (TypeError, ValueError):
        level = 1
    sheet["level"] = level
    prof = proficiency_bonus(level)
    sheet["proficiency_bonus"] = prof

    # atributos
    base = build.get("base_abilities")
    scores: dict[str, int] | None = None
    if not base:
        method = build.get("method")
        missing.append(
            "abilities (pergunte: rolar 4d6, conjunto padrão, compra de pontos ou manual; depois distribua)"
            if not method else "abilities (distribua os valores nos seis atributos)"
        )
    else:
        bonus, falta_bonus = racial_bonuses(race_key, race, parse_scores(build.get("race_bonuses") or {}))
        if falta_bonus:
            missing.append(falta_bonus)
        scores = {a: min(20, int(base[a]) + bonus.get(a, 0)) for a in ABILITIES}
        sheet["attributes"] = scores
        sheet["base_attributes"] = dict(base)
        sheet["racial_bonuses"] = bonus

    # perícias e salvaguardas
    skills: dict[str, dict[str, Any]] = {}
    if bg is not None:
        bg_skills = list(bg["skills"])
    else:
        bg_skills = [s for s in (skill_key(x) for x in build.get("background_skills") or []) if s][:2]
        if bg_name and len(bg_skills) < 2:
            missing.append("background_skills (antecedente próprio: escolha 2 perícias)")
    race_skills = list(race.get("skills", [])) if race else []
    for s in bg_skills + race_skills:
        skills[s] = {"proficient": True, "source": "antecedente" if s in bg_skills else "raça"}
    if cls is not None:
        sheet["saving_throws"] = {a: {"proficient": a in cls["saves"]} for a in ABILITIES}
        n, opcoes = cls["skills"]
        escolhidas = []
        for raw in build.get("class_skills") or []:
            s = skill_key(raw)
            if s is None or s not in opcoes:
                raise BuildError(f"Perícia fora da lista do {cls['name']}: {raw}. Opções: {', '.join(opcoes)}.")
            if s in skills:
                raise BuildError(f"{raw} já vem do antecedente/raça — escolha outra perícia.")
            if s not in escolhidas:
                escolhidas.append(s)
        if len(escolhidas) > n:
            raise BuildError(f"{cls['name']} escolhe {n} perícia(s), não {len(escolhidas)}.")
        if len(escolhidas) < n:
            livres = [o for o in opcoes if o not in skills]
            missing.append(f"class_skills ({cls['name']}: escolha {n} entre {', '.join(livres)})")
        for s in escolhidas:
            skills[s] = {"proficient": True, "source": "classe"}
        pericia = cls.get("expertise", 0)
        if pericia:
            exp = [skill_key(x) for x in build.get("expertise") or []]
            exp = [s for s in exp if s and s in skills][:pericia]
            if len(exp) < pericia and len(escolhidas) == n:
                missing.append(f"expertise (Ladino: {pericia} perícias proficientes para dobrar o bônus)")
            for s in exp:
                skills[s] = {**skills[s], "proficiency": 2}
    sheet["skills"] = skills

    if cls is not None and scores is not None:
        con = modifier(scores["constitution"])
        extra = race.get("hp_per_level", 0) if race else 0
        hp, rolls = _hp_for_level(cls["hit_die"], con, level, str(build.get("hp_method") or "average"),
                                  list(build.get("hp_rolls") or []), extra, roller)
        build["hp_rolls"] = rolls
        sheet["hp"] = {"current": hp, "max": hp}
        sheet["hit_dice"] = {"die": f"d{cls['hit_die']}", "total": level, "current": level}
        ac, fonte = _armor_class(cls, scores)
        sheet["armor_class"] = ac
        sheet["armor_source"] = fonte
        sheet["speed"] = race["speed"] if race else 30
        sheet["initiative"] = modifier(scores["dexterity"])
        percep = skills.get("perception", {})
        rank = percep.get("proficiency", 1) if percep else 0
        sheet["passive_perception"] = 10 + modifier(scores["wisdom"]) + prof * rank
        sheet["attacks"] = _attacks(cls, scores, prof)
        caster = cls.get("caster")
        if caster:
            mod = modifier(scores[caster["ability"]])
            cantrips, spells = _spell_limits(caster, scores, level)
            sheet["spellcasting"] = {
                "ability": caster["ability"], "save_dc": 8 + prof + mod, "attack_modifier": prof + mod,
                "cantrips_known": cantrips, "spells_known": spells,
            }
            sheet["spell_attack_modifier"] = prof + mod
            sheet["spell_slots"] = spell_slots(caster, level)
            sheet["spells"] = _spells(build.get("spells"), caster, scores, level, prof)
    return sheet, missing, build


def starting_equipment(build: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Itens iniciais (classe) + ouro (antecedente). Armadura e escudo já vêm vestidos."""
    _, cls = find_class(build.get("class"))
    if cls is None:
        return [], 0
    itens: list[dict[str, Any]] = []
    if cls.get("armor"):
        armor = _ARMOR[cls["armor"]]
        itens.append({"name": armor["name"], "kind": "armor", "weight": armor["weight"], "quantity": 1,
                      "equipped": True, "slot": "armor"})
    if cls.get("shield"):
        itens.append({**_SHIELD, "equipped": True, "slot": "off_hand"})
    for i, weapon in enumerate(cls["weapons"]):
        itens.append({**weapon, "equipped": i == 0, "slot": "main_hand" if i == 0 else None})
    itens.extend(dict(g) for g in cls["gear"])
    _, bg = find_background(build.get("background"))
    gold = (bg or {}).get("gold", 10)
    return itens, gold


def options() -> dict[str, Any]:
    """O cardápio que o narrador apresenta ao jogador."""
    return {
        "ability_methods": {
            "roll": "4d6, descarta o menor, seis vezes — rolado pelo servidor (roll_abilities)",
            "standard_array": list(STANDARD_ARRAY),
            "point_buy": f"{POINT_BUY_BUDGET} pontos, valores 8–15 (custos {POINT_BUY_COST})",
            "manual": "valores escolhidos pelo jogador (3–18), se a mesa permitir",
        },
        "classes": {
            c["name"]: {"hit_die": f"d{c['hit_die']}", "saves": [ABILITY_PT[a] for a in c["saves"]],
                        "skills": f"{c['skills'][0]} de: {', '.join(c['skills'][1])}",
                        "caster": (c.get("caster") or {}).get("ability")}
            for c in CLASSES.values()
        },
        "races": {r["name"]: {ABILITY_PT[a]: f"+{v}" for a, v in r["bonuses"].items()} for r in RACES.values()},
        "backgrounds": {b["name"]: b["skills"] for b in BACKGROUNDS.values()},
        "custom": "ancestralidade/antecedente inventados pela campanha valem: ancestralidade dá +2/+1 "
                  "(race_bonuses) e antecedente dá 2 perícias à escolha (background_skills)",
    }
