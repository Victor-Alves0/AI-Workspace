"""Combate por turnos do Imaginai (D&D 5e) — o núcleo, sem banco.

O servidor decide TUDO que é mecânico no combate: quem entra, a ordem de iniciativa, a
ação de cada criatura no turno dela e quando o combate acaba. O narrador só narra o
que sai daqui. É puro de propósito: recebe estados, devolve estados, e o dado é
injetável — então o combate inteiro é testável sem banco e sem sorte.

Três lados: o jogador, os ALIADOS (NPCs lutando junto, agem no turno deles contra os
hostis) e os HOSTIS (agem contra o jogador e os aliados). Cada criatura pode ter várias
ações — ataque (d20 contra a CA) ou habilidade com TESTE DE RESISTÊNCIA (baforada,
magia, veneno: CD fixa, meio dano se passar, condição se falhar) — e as CONDIÇÕES
(envenenado, caído, atordoado...) mudam os dados: vantagem, desvantagem, turno perdido,
resistência que falha sozinha, acerto crítico garantido.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

# (lados) -> 1..lados. Produção usa `secrets`; testes injetam uma sequência fixa.
Roller = Callable[[int], int]

_DICE_RE = re.compile(r"^\s*(?:(\d+)d(\d+)|(\d+))\s*(?:([+-]\s*\d+))?\s*$")

# Criatura criada sem ataque cadastrado ainda precisa lutar: um ataque modesto, no
# nível de um goblin com cimitarra (+4, 1d6+2 no livro), um pouco abaixo de propósito.
DEFAULT_ATTACK: dict[str, Any] = {
    "key": "strike", "name": "Ataque", "attack_modifier": 3,
    "damage": "1d6+1", "damage_type": "",
}

ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
_ABILITY_ALIASES = {
    "for": "strength", "str": "strength", "forca": "strength",
    "des": "dexterity", "dex": "dexterity", "destreza": "dexterity",
    "con": "constitution", "constituicao": "constitution",
    "int": "intelligence", "inteligencia": "intelligence",
    "sab": "wisdom", "wis": "wisdom", "sabedoria": "wisdom",
    "car": "charisma", "cha": "charisma", "carisma": "charisma",
}

# --------------------------------------------------------------------------- #
# Condições (PHB, apêndice A) — só o que muda dados                            #
# --------------------------------------------------------------------------- #
#   attack_dis / attack_adv   → os ATAQUES de quem tem a condição
#   attacked_adv / attacked_dis → ataques CONTRA quem tem a condição
#   check_dis                 → testes de atributo de quem tem a condição
#   incapacitated             → perde o turno
#   auto_fail                 → resistências que falham sozinhas
#   dex_save_dis              → desvantagem em resistência de Destreza
#   auto_crit                 → acerto contra ele é crítico (alcance corpo a corpo)
CONDITIONS: dict[str, dict[str, Any]] = {
    "poisoned": {"label": "envenenado", "attack_dis": True, "check_dis": True},
    "frightened": {"label": "amedrontado", "attack_dis": True, "check_dis": True},
    "blinded": {"label": "cego", "attack_dis": True, "attacked_adv": True},
    "prone": {"label": "caído", "attack_dis": True, "attacked_adv": True},
    "restrained": {"label": "contido", "attack_dis": True, "attacked_adv": True, "dex_save_dis": True},
    "invisible": {"label": "invisível", "attack_adv": True, "attacked_dis": True},
    "incapacitated": {"label": "incapacitado", "incapacitated": True},
    "stunned": {"label": "atordoado", "incapacitated": True, "attacked_adv": True,
                "auto_fail": ("strength", "dexterity")},
    "paralyzed": {"label": "paralisado", "incapacitated": True, "attacked_adv": True,
                  "auto_fail": ("strength", "dexterity"), "auto_crit": True},
    "unconscious": {"label": "inconsciente", "incapacitated": True, "attacked_adv": True,
                    "auto_fail": ("strength", "dexterity"), "auto_crit": True},
    "petrified": {"label": "petrificado", "incapacitated": True, "attacked_adv": True,
                  "auto_fail": ("strength", "dexterity")},
    "charmed": {"label": "enfeitiçado"},
    "grappled": {"label": "agarrado"},
    "deafened": {"label": "surdo"},
    "exhaustion": {"label": "exaustão", "check_dis": True},
}
_CONDITION_ALIASES = {
    "envenenado": "poisoned", "amedrontado": "frightened", "assustado": "frightened",
    "cego": "blinded", "caido": "prone", "derrubado": "prone", "contido": "restrained",
    "impedido": "restrained", "preso": "restrained", "invisivel": "invisible",
    "incapacitado": "incapacitated", "atordoado": "stunned", "paralisado": "paralyzed",
    "inconsciente": "unconscious", "petrificado": "petrified", "enfeiticado": "charmed",
    "encantado": "charmed", "agarrado": "grappled", "surdo": "deafened", "exausto": "exhaustion",
    "exaustao": "exhaustion",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return text.strip().casefold()


def condition_key(value: Any) -> str | None:
    key = _norm(value).replace(" ", "_")
    if key in CONDITIONS:
        return key
    return _CONDITION_ALIASES.get(_norm(value))


def ability_key(value: Any) -> str | None:
    key = _norm(value)
    if key in ABILITIES:
        return key
    return _ABILITY_ALIASES.get(key)


def secure_roller(sides: int) -> int:
    return secrets.randbelow(max(2, sides)) + 1


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def dnd_state(state: Any) -> dict[str, Any]:
    """A ficha fica em `state.dnd5e` no personagem e às vezes na raiz nas criaturas."""
    if not isinstance(state, dict):
        return {}
    inner = state.get("dnd5e")
    return inner if isinstance(inner, dict) else state


def hp_of(state: Any) -> tuple[int, int] | None:
    """(atual, máximo), ou None se a entidade não tem estatísticas de combate."""
    hp = dnd_state(state).get("hp")
    if not isinstance(hp, dict) or "max" not in hp:
        return None
    maximum = max(0, _int(hp.get("max")))
    current = max(0, min(_int(hp.get("current"), maximum), maximum))
    return current, maximum


def is_hostile(state: Any) -> bool:
    """`hostile` é gravado pelo construtor de mundo e por quem é atacado."""
    s = state if isinstance(state, dict) else {}
    return bool(s.get("hostile") or dnd_state(state).get("hostile"))


def is_ally(state: Any) -> bool:
    """NPC que luta ao lado do jogador (`ally`, gravado pela ação `ally` do narrador)."""
    s = state if isinstance(state, dict) else {}
    return bool(s.get("ally") or dnd_state(state).get("ally")) and not is_hostile(state)


def armor_class(state: Any) -> int:
    return max(1, _int(dnd_state(state).get("armor_class"), 10))


def _score(value: Any) -> int:
    if isinstance(value, dict):
        value = value.get("score", 10)
    return _int(value, 10)


def initiative_bonus(state: Any) -> int:
    """Modificador de Destreza. Criatura sem atributos entra com +0."""
    attributes = dnd_state(state).get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    return (_score(attributes.get("dexterity", 10)) - 10) // 2


def save_modifiers(state: Any) -> dict[str, int]:
    """Bônus de resistência por atributo: modificador + proficiência onde a ficha marca.
    `saves` explícito (bloco de estatísticas de criatura) tem precedência."""
    dnd = dnd_state(state)
    explicit = dnd.get("saves") if isinstance(dnd.get("saves"), dict) else {}
    attributes = dnd.get("attributes") if isinstance(dnd.get("attributes"), dict) else {}
    profs = dnd.get("saving_throws") if isinstance(dnd.get("saving_throws"), dict) else {}
    prof = _int(dnd.get("proficiency_bonus"), 2)
    out: dict[str, int] = {}
    for ability in ABILITIES:
        chave = next((k for k in explicit if ability_key(k) == ability), None)
        if chave is not None:
            out[ability] = _int(explicit[chave])
            continue
        mod = (_score(attributes.get(ability, 10)) - 10) // 2
        marcado = profs.get(ability)
        proficient = marcado is True or (isinstance(marcado, dict) and marcado.get("proficient"))
        out[ability] = mod + (prof if proficient else 0)
    return out


def conditions_of(state: Any) -> tuple[dict[str, Any], ...]:
    raw = dnd_state(state).get("conditions")
    out = []
    for item in raw if isinstance(raw, list) else []:
        record = item if isinstance(item, dict) else {"key": item}
        key = condition_key(record.get("key") or record.get("name"))
        if key:
            rounds = record.get("rounds")
            out.append({"key": key, "rounds": _int(rounds) if rounds is not None else None})
    return tuple(out)


def _normalize_action(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    base = {
        "key": str(raw.get("key") or "attack"),
        "name": str(raw.get("name") or raw.get("key") or "Ataque"),
        "damage": str(raw.get("damage") or ""),
        "damage_type": str(raw.get("damage_type") or ""),
    }
    condicao = condition_key(raw.get("condition"))
    if condicao:
        base["condition"] = condicao
        base["condition_rounds"] = max(1, min(_int(raw.get("condition_rounds") or raw.get("rounds"), 1), 10))
    save = ability_key(raw.get("save"))
    if save:
        return {**base, "type": "save", "save": save, "dc": max(5, min(_int(raw.get("dc"), 12), 30)),
                "half": raw.get("half", True) is not False}
    if not base["damage"] and not condicao:
        return None
    return {**base, "type": "attack", "attack_modifier": _int(raw.get("attack_modifier"), 0)}


def actions_of(state: Any) -> tuple[dict[str, Any], ...]:
    """Tudo o que a criatura sabe fazer no turno: ataques e habilidades com resistência."""
    attacks = dnd_state(state).get("attacks")
    if isinstance(attacks, dict):
        attacks = [{"key": str(k), **(v if isinstance(v, dict) else {})} for k, v in attacks.items()]
    out = [a for a in (_normalize_action(r) for r in (attacks if isinstance(attacks, list) else [])) if a]
    return tuple(out) or ({**DEFAULT_ATTACK, "type": "attack"},)


def attack_profile(state: Any) -> dict[str, Any]:
    """A primeira ação de ATAQUE da criatura (ou o ataque padrão)."""
    for action in actions_of(state):
        if action["type"] == "attack" and action.get("damage"):
            return {k: action[k] for k in ("key", "name", "attack_modifier", "damage", "damage_type")}
    return dict(DEFAULT_ATTACK)


def roll_damage(expression: str, roller: Roller, *, critical: bool = False) -> tuple[int, list[int]]:
    """Rola `XdY+Z` (ou valor fixo). Crítico dobra os DADOS, não o modificador — regra
    do 5e. Expressão inválida vira o dano padrão em vez de derrubar o turno inimigo."""
    match = _DICE_RE.fullmatch(expression or "")
    if match is None:
        match = _DICE_RE.fullmatch(DEFAULT_ATTACK["damage"])
    count_raw, sides_raw, flat_raw, modifier_raw = match.groups()
    modifier = _int((modifier_raw or "0").replace(" ", ""))
    if flat_raw is not None:
        return max(0, _int(flat_raw) + modifier), []
    count = max(1, min(_int(count_raw, 1), 20)) * (2 if critical else 1)
    sides = max(2, min(_int(sides_raw, 6), 100))
    rolls = [roller(sides) for _ in range(count)]
    return max(0, sum(rolls) + modifier), rolls


def health_label(current: int, maximum: int) -> str:
    """Como o JOGADOR enxerga a vida do inimigo: estado aparente, não o número."""
    if maximum <= 0:
        return "desconhecido"
    if current <= 0:
        return "caído"
    ratio = current / maximum
    if ratio >= 1:
        return "ileso"
    if ratio > 0.5:
        return "ferido"
    return "gravemente ferido"


# --------------------------------------------------------------------------- #
# Combatentes                                                                  #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Fighter:
    id: str
    name: str
    side: str                      # "player" | "ally" | "hostile"
    hp: int
    hp_max: int
    ac: int
    location_id: str | None
    initiative_bonus: int = 0
    attack: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ATTACK))
    actions: tuple[dict[str, Any], ...] = ()
    conditions: tuple[dict[str, Any], ...] = ()
    saves: dict[str, int] = field(default_factory=dict)

    @property
    def down(self) -> bool:
        return self.hp <= 0

    def has(self, flag: str) -> bool:
        return any(CONDITIONS.get(c["key"], {}).get(flag) for c in self.conditions)

    def auto_fails(self, ability: str) -> bool:
        return any(ability in CONDITIONS.get(c["key"], {}).get("auto_fail", ()) for c in self.conditions)

    @property
    def incapacitated(self) -> bool:
        return self.has("incapacitated")

    def all_actions(self) -> tuple[dict[str, Any], ...]:
        if self.actions:
            return self.actions
        return ({"type": "attack", **self.attack},)


def fighter_from(entity_id: str, name: str, state: Any, location_id: str | None,
                 side: str) -> Fighter | None:
    """Monta o combatente a partir da entidade. Sem HP cadastrado não há como lutar."""
    vida = hp_of(state)
    if vida is None:
        return None
    current, maximum = vida
    return Fighter(
        id=str(entity_id), name=name or "?", side=side, hp=current, hp_max=maximum,
        ac=armor_class(state), location_id=str(location_id) if location_id else None,
        initiative_bonus=initiative_bonus(state), attack=attack_profile(state),
        actions=actions_of(state), conditions=conditions_of(state), saves=save_modifiers(state),
    )


def add_condition(conditions: tuple[dict[str, Any], ...], key: str, rounds: int | None) -> tuple[dict[str, Any], ...]:
    """Aplica (ou renova pela maior duração) uma condição."""
    atual = [c for c in conditions if c["key"] != key]
    anterior = next((c for c in conditions if c["key"] == key), None)
    if anterior is not None and rounds is not None and anterior.get("rounds") is not None:
        rounds = max(rounds, anterior["rounds"])
    elif anterior is not None and anterior.get("rounds") is None:
        rounds = None
    return (*atual, {"key": key, "rounds": rounds})


def tick_conditions(conditions: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Fim do turno de quem tem as condições: as com duração perdem uma rodada."""
    out = []
    for c in conditions:
        if c.get("rounds") is None:
            out.append(c)
        elif c["rounds"] > 1:
            out.append({**c, "rounds": c["rounds"] - 1})
    return tuple(out)


def roll_initiative(fighters: list[Fighter], roller: Roller) -> list[dict[str, Any]]:
    """d20 + Destreza para cada um; maior age primeiro. Empate: maior bônus; depois o
    jogador (a mesa do 5e costuma favorecer o PJ no empate)."""
    rolled = []
    for fighter in fighters:
        die = roller(20)
        rolled.append({
            "id": fighter.id, "name": fighter.name, "side": fighter.side,
            "die": die, "initiative": die + fighter.initiative_bonus,
            "bonus": fighter.initiative_bonus,
        })
    rolled.sort(key=lambda r: (r["initiative"], r["bonus"], r["side"] == "player"), reverse=True)
    return rolled


def start(fighters: list[Fighter], player_id: str, roller: Roller, *,
          player_already_acted: bool) -> dict[str, Any]:
    """Novo encontro. `player_already_acted`: o combate começou PELO ataque do jogador,
    então esse ataque conta como o turno dele nesta rodada."""
    order = roll_initiative(fighters, roller)
    turn = 0
    if player_already_acted:
        posicao = next((i for i, r in enumerate(order) if r["id"] == player_id), 0)
        turn = posicao + 1
    return {"active": True, "round": 1, "turn": turn, "order": order, "outcome": None}


def end_player_turn(encounter: dict[str, Any], player_id: str) -> dict[str, Any]:
    """O jogador acabou de agir: o ponteiro sai da vez DELE para o próximo."""
    enc = {**encounter}
    order = enc.get("order") or []
    turn = int(enc.get("turn") or 0)
    if 0 <= turn < len(order) and order[turn].get("id") == player_id:
        enc["turn"] = turn + 1
    return enc


# --------------------------------------------------------------------------- #
# Turnos das criaturas                                                         #
# --------------------------------------------------------------------------- #
@dataclass
class TurnReport:
    """O que aconteceu nos turnos das criaturas — vira evento e texto p/ o narrador."""
    enemy_turns: list[dict[str, Any]]
    fighters: dict[str, Fighter]
    encounter: dict[str, Any]


def _outcome(fighters: dict[str, Fighter], player_id: str) -> str | None:
    player = fighters.get(player_id)
    if player is None or player.down:
        return "defeat"
    inimigos = [
        f for f in fighters.values()
        if f.side == "hostile" and not f.down and f.location_id == player.location_id
    ]
    if inimigos:
        return None
    vivos_longe = any(f.side == "hostile" and not f.down for f in fighters.values())
    return "escaped" if vivos_longe else "victory"


def _opponents(actor: Fighter, pool: dict[str, Fighter]) -> list[Fighter]:
    """Hostil mira jogador e aliados; aliado mira hostis. Só quem está de pé e no local."""
    lados = {"player", "ally"} if actor.side == "hostile" else {"hostile"}
    return [
        f for f in pool.values()
        if f.side in lados and not f.down and f.location_id == actor.location_id
    ]


def _pick(items: list, roller: Roller) -> Any:
    """Escolha por dado (determinística nos testes); com uma opção, não gasta dado."""
    return items[0] if len(items) == 1 else items[roller(len(items)) - 1]


def _d20(mode: str, roller: Roller) -> tuple[int, list[int]]:
    rolls = [roller(20)]
    if mode != "normal":
        rolls.append(roller(20))
    die = max(rolls) if mode == "advantage" else min(rolls) if mode == "disadvantage" else rolls[0]
    return die, rolls


def attack_mode(attacker: Fighter | None, target: Fighter | None, requested: str = "normal") -> tuple[str, list[str]]:
    """Vantagem/desvantagem pelo que as condições dizem (e o pedido do narrador).
    Uma de cada se anulam — regra do 5e."""
    adv, dis, motivos = requested == "advantage", requested == "disadvantage", []
    if attacker is not None:
        for c in attacker.conditions:
            regra = CONDITIONS.get(c["key"], {})
            if regra.get("attack_adv"):
                adv = True
                motivos.append(f"{regra['label']} (atacante)")
            if regra.get("attack_dis"):
                dis = True
                motivos.append(f"{regra['label']} (atacante)")
    if target is not None:
        for c in target.conditions:
            regra = CONDITIONS.get(c["key"], {})
            if regra.get("attacked_adv"):
                adv = True
                motivos.append(f"{regra['label']} (alvo)")
            if regra.get("attacked_dis"):
                dis = True
                motivos.append(f"{regra['label']} (alvo)")
    if adv and not dis:
        return "advantage", motivos
    if dis and not adv:
        return "disadvantage", motivos
    return "normal", motivos


def roll_save(target: Fighter, ability: str, dc: int, roller: Roller,
              requested: str = "normal") -> dict[str, Any]:
    """Teste de resistência no servidor: d20 + bônus; condições podem falhar sozinhas."""
    if target.auto_fails(ability):
        return {"ability": ability, "dc": dc, "rolls": [], "die": None, "modifier": 0,
                "total": None, "success": False, "auto_fail": True}
    mode = requested
    if ability == "dexterity" and target.has("dex_save_dis"):
        mode = "normal" if mode == "advantage" else "disadvantage"
    die, rolls = _d20(mode, roller)
    mod = target.saves.get(ability, 0)
    total = die + mod
    return {"ability": ability, "dc": dc, "rolls": rolls, "die": die, "modifier": mod,
            "total": total, "success": total >= dc, "auto_fail": False, "mode": mode}


def _resolve_action(actor: Fighter, target: Fighter, action: dict[str, Any],
                    roller: Roller) -> tuple[dict[str, Any], Fighter]:
    """Uma ação contra um alvo. Devolve (registro do turno, alvo atualizado)."""
    record: dict[str, Any] = {
        "attacker_id": actor.id, "attacker": actor.name, "attacker_side": actor.side,
        "target_id": target.id, "target": target.name, "target_side": target.side,
        "attack_name": action.get("name") or "Ataque", "kind": action["type"],
        "damage_type": action.get("damage_type") or "", "damage": 0, "damage_rolls": [],
        "condition_applied": None,
    }
    novo = target
    if action["type"] == "save":
        save = roll_save(target, action["save"], int(action["dc"]), roller)
        dano, dados = (0, [])
        if action.get("damage"):
            cheio, dados = roll_damage(action["damage"], roller)
            dano = cheio if not save["success"] else (cheio // 2 if action.get("half", True) else 0)
        record.update(save=save, saved=save["success"], hit=not save["success"], damage=dano,
                      damage_rolls=dados, critical=False)
        if not save["success"] and action.get("condition"):
            record["condition_applied"] = action["condition"]
    else:
        mode, motivos = attack_mode(actor, target)
        modificador = _int(action.get("attack_modifier"), 0)
        die, rolls = _d20(mode, roller)
        total = die + modificador
        acertou = die == 20 or (die != 1 and total >= target.ac)
        critico = die == 20 or (acertou and target.has("auto_crit"))
        dano, dados = (0, [])
        if acertou and action.get("damage"):
            dano, dados = roll_damage(str(action["damage"]), roller, critical=critico)
        record.update(die=die, rolls=rolls, mode=mode, mode_reasons=motivos, modifier=modificador,
                      total=total, target_ac=target.ac, hit=acertou, critical=critico,
                      damage=dano, damage_rolls=dados)
        if acertou and action.get("condition"):
            record["condition_applied"] = action["condition"]
    if record["damage"]:
        novo = replace(novo, hp=max(0, novo.hp - record["damage"]))
    if record["condition_applied"]:
        novo = replace(novo, conditions=add_condition(
            novo.conditions, record["condition_applied"], action.get("condition_rounds", 1)))
    if novo.down and not target.down:
        # quem cai fica inconsciente (e o que for hostil para de lutar)
        novo = replace(novo, conditions=add_condition(novo.conditions, "unconscious", None))
    record["target_hp_after"] = novo.hp
    if target.side in {"player", "ally"}:
        record["target_hp"] = novo.hp
        record["target_hp_max"] = novo.hp_max
    else:
        record["target_health"] = health_label(novo.hp, novo.hp_max)
    return record, novo


def run_enemy_turns(encounter: dict[str, Any], fighters: dict[str, Fighter],
                    player_id: str, roller: Roller) -> TurnReport:
    """Avança a ordem a partir do ponteiro e resolve o turno de cada criatura (hostis e
    aliados) até a vez do jogador voltar — ou o combate acabar."""
    enc = {**encounter, "order": list(encounter.get("order") or [])}
    pool = dict(fighters)
    turns: list[dict[str, Any]] = []
    order = enc["order"]
    if not order or not enc.get("active"):
        return TurnReport([], pool, enc)

    for _ in range(len(order) * 2 + 2):          # teto: ordem corrompida não vira laço
        fim = _outcome(pool, player_id)
        if fim is not None:
            enc.update(active=False, outcome=fim)
            break
        if enc["turn"] >= len(order):
            enc["turn"] = 0
            enc["round"] = int(enc.get("round") or 1) + 1
        vez = order[enc["turn"]]
        if vez["id"] == player_id:
            break
        enc["turn"] += 1
        ator = pool.get(vez["id"])
        if ator is None or ator.down or ator.side not in {"hostile", "ally"}:
            continue
        if ator.incapacitated:
            turns.append({"attacker_id": ator.id, "attacker": ator.name, "attacker_side": ator.side,
                          "kind": "skipped", "reason": ", ".join(
                              CONDITIONS[c["key"]]["label"] for c in ator.conditions
                              if CONDITIONS.get(c["key"], {}).get("incapacitated")),
                          "damage": 0})
        else:
            alvos = _opponents(ator, pool)
            if alvos:
                alvo = _pick(alvos, roller)
                acoes = ator.all_actions()
                acao = acoes[(int(enc.get("round") or 1) - 1) % len(acoes)]
                registro, novo = _resolve_action(ator, alvo, acao, roller)
                pool[alvo.id] = novo
                turns.append(registro)
        pool[ator.id] = replace(pool[ator.id], conditions=tick_conditions(pool[ator.id].conditions))
    else:
        enc.update(active=False, outcome=enc.get("outcome") or "stalled")
    return TurnReport(turns, pool, enc)


def public_view(encounter: dict[str, Any] | None, fighters: dict[str, Fighter],
                player_id: str) -> dict[str, Any] | None:
    """Encontro como o JOGADOR o vê: vida própria e dos aliados em números, a dos
    inimigos como estado aparente, condições de todos e de quem é a vez."""
    if not encounter:
        return None
    order = encounter.get("order") or []
    turn = int(encounter.get("turn") or 0)
    atual = order[turn]["id"] if encounter.get("active") and 0 <= turn < len(order) else None
    linhas = []
    for entry in order:
        fighter = fighters.get(entry["id"])
        linha: dict[str, Any] = {
            "id": entry["id"], "name": entry["name"],
            "side": fighter.side if fighter is not None else entry["side"],
            "initiative": entry["initiative"], "current": entry["id"] == atual,
        }
        if fighter is not None:
            if fighter.side in {"player", "ally"}:
                linha.update(hp=fighter.hp, hp_max=fighter.hp_max)
            linha["health"] = health_label(fighter.hp, fighter.hp_max)
            linha["conditions"] = [
                {"key": c["key"], "label": CONDITIONS.get(c["key"], {}).get("label", c["key"]),
                 "rounds": c.get("rounds")}
                for c in fighter.conditions
            ]
        linhas.append(linha)
    return {
        "active": bool(encounter.get("active")),
        "round": int(encounter.get("round") or 1),
        "outcome": encounter.get("outcome"),
        "order": linhas,
    }
