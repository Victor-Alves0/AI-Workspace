"""Combate por turnos do Imaginai (D&D 5e) — o núcleo, sem banco.

Antes, o combate era unilateral: o jogador atacava e o HP do alvo caía, mas nada fazia
o inimigo revidar. O narrador podia DESCREVER o goblin acertando, só que o HP do
personagem não mudava — quebrando a promessa central do Imaginai, de que o mundo é
autoritativo e o modelo narra, mas não decide resultados.

Este módulo decide TUDO que é mecânico no combate: quem entra, a ordem de iniciativa,
o ataque de cada inimigo no turno dele e quando o combate acaba. É puro de propósito:
recebe estados, devolve estados, e o dado é injetável — então o combate inteiro é
testável sem banco e sem sorte. `service.py` só persiste o que sai daqui.
"""

from __future__ import annotations

import re
import secrets
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


def armor_class(state: Any) -> int:
    return max(1, _int(dnd_state(state).get("armor_class"), 10))


def initiative_bonus(state: Any) -> int:
    """Modificador de Destreza. Criatura sem atributos entra com +0."""
    attributes = dnd_state(state).get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    dexterity = attributes.get("dexterity", 10)
    if isinstance(dexterity, dict):
        dexterity = dexterity.get("score", 10)
    return (_int(dexterity, 10) - 10) // 2


def attack_profile(state: Any) -> dict[str, Any]:
    """O ataque que um inimigo usa no turno dele: o primeiro cadastrado, ou o padrão."""
    attacks = dnd_state(state).get("attacks")
    if isinstance(attacks, dict):
        attacks = [
            {"key": str(key), **(value if isinstance(value, dict) else {})}
            for key, value in attacks.items()
        ]
    if isinstance(attacks, list):
        for raw in attacks:
            if isinstance(raw, dict) and raw.get("damage"):
                return {
                    "key": str(raw.get("key") or "attack"),
                    "name": str(raw.get("name") or raw.get("key") or "Ataque"),
                    "attack_modifier": _int(raw.get("attack_modifier"), 0),
                    "damage": str(raw["damage"]),
                    "damage_type": str(raw.get("damage_type") or ""),
                }
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
    """Como o JOGADOR enxerga a vida do inimigo. No 5e o mestre não diz o HP exato;
    diz o estado aparente — e é isso que a interface e o narrador recebem."""
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
# Combatentes e encontro                                                       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Fighter:
    id: str
    name: str
    side: str                      # "player" | "hostile"
    hp: int
    hp_max: int
    ac: int
    location_id: str | None
    initiative_bonus: int = 0
    attack: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_ATTACK))

    @property
    def down(self) -> bool:
        return self.hp <= 0


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
    )


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
    então esse ataque conta como o turno dele nesta rodada — o ponteiro vai para logo
    depois dele. Numa emboscada, começa do topo da ordem."""
    order = roll_initiative(fighters, roller)
    turn = 0
    if player_already_acted:
        posicao = next((i for i, r in enumerate(order) if r["id"] == player_id), 0)
        turn = posicao + 1
    return {"active": True, "round": 1, "turn": turn, "order": order, "outcome": None}


def end_player_turn(encounter: dict[str, Any], player_id: str) -> dict[str, Any]:
    """O jogador acabou de agir: o ponteiro sai da vez DELE para o próximo. Se o ponteiro
    não estava no jogador (estado antigo, reprocessamento), fica onde está — avançar às
    cegas pularia o turno de um inimigo."""
    enc = {**encounter}
    order = enc.get("order") or []
    turn = int(enc.get("turn") or 0)
    if 0 <= turn < len(order) and order[turn].get("id") == player_id:
        enc["turn"] = turn + 1
    return enc


@dataclass
class TurnReport:
    """O que aconteceu nos turnos inimigos — vira evento no ledger e texto p/ o narrador."""
    enemy_turns: list[dict[str, Any]]
    fighters: dict[str, Fighter]
    encounter: dict[str, Any]


def _outcome(encounter: dict, fighters: dict[str, Fighter], player_id: str) -> str | None:
    player = fighters.get(player_id)
    if player is None or player.down:
        return "defeat"
    inimigos = [
        f for f in fighters.values()
        if f.side == "hostile" and not f.down
        and f.location_id == player.location_id
    ]
    if inimigos:
        return None
    # ninguém de pé no mesmo lugar: ou venceu, ou se afastou do combate
    vivos_longe = any(f.side == "hostile" and not f.down for f in fighters.values())
    return "escaped" if vivos_longe else "victory"


def run_enemy_turns(encounter: dict[str, Any], fighters: dict[str, Fighter],
                    player_id: str, roller: Roller) -> TurnReport:
    """Avança a ordem a partir do ponteiro atual e resolve cada turno inimigo até a vez
    do jogador voltar — ou o combate acabar. Nada aqui é decidido pelo modelo."""
    enc = {**encounter, "order": list(encounter.get("order") or [])}
    pool = dict(fighters)
    turns: list[dict[str, Any]] = []
    order = enc["order"]
    if not order or not enc.get("active"):
        return TurnReport([], pool, enc)

    # teto defensivo: uma ordem corrompida nunca vira laço infinito
    for _ in range(len(order) * 2 + 2):
        fim = _outcome(enc, pool, player_id)
        if fim is not None:
            enc.update(active=False, outcome=fim)
            break
        if enc["turn"] >= len(order):
            enc["turn"] = 0
            enc["round"] = int(enc.get("round") or 1) + 1
        vez = order[enc["turn"]]
        if vez["id"] == player_id:
            break                                # a vez voltou ao jogador
        atacante = pool.get(vez["id"])
        enc["turn"] += 1
        alvo = pool.get(player_id)
        if atacante is None or atacante.down or atacante.side != "hostile" or alvo is None:
            continue
        if atacante.location_id != alvo.location_id:
            continue                             # o jogador saiu do alcance
        turns.append(_enemy_attack(atacante, alvo, roller))
        dano = turns[-1]["damage"]
        if dano:
            pool[player_id] = replace(alvo, hp=max(0, alvo.hp - dano))
        turns[-1]["target_hp"] = pool[player_id].hp
        turns[-1]["target_hp_max"] = pool[player_id].hp_max
    else:
        enc.update(active=False, outcome=enc.get("outcome") or "stalled")
    return TurnReport(turns, pool, enc)


def _enemy_attack(atacante: Fighter, alvo: Fighter, roller: Roller) -> dict[str, Any]:
    """d20 + ataque contra a CA do jogador. 20 natural é crítico; 1 natural erra sempre."""
    ataque = atacante.attack
    modificador = _int(ataque.get("attack_modifier"), 0)
    die = roller(20)
    total = die + modificador
    critico = die == 20
    acertou = critico or (die != 1 and total >= alvo.ac)
    dano, dados = (0, [])
    if acertou:
        dano, dados = roll_damage(str(ataque.get("damage") or ""), roller, critical=critico)
    return {
        "attacker_id": atacante.id, "attacker": atacante.name,
        "attack_name": ataque.get("name") or "Ataque",
        "die": die, "modifier": modificador, "total": total, "target_ac": alvo.ac,
        "hit": acertou, "critical": critico,
        "damage": dano, "damage_rolls": dados,
        "damage_type": ataque.get("damage_type") or "",
    }


def public_view(encounter: dict[str, Any] | None, fighters: dict[str, Fighter],
                player_id: str) -> dict[str, Any] | None:
    """Encontro como o JOGADOR o vê: a própria vida em números, a dos inimigos como
    estado aparente, e de quem é a vez."""
    if not encounter:
        return None
    order = encounter.get("order") or []
    turn = int(encounter.get("turn") or 0)
    atual = order[turn]["id"] if encounter.get("active") and 0 <= turn < len(order) else None
    linhas = []
    for entry in order:
        fighter = fighters.get(entry["id"])
        linha: dict[str, Any] = {
            "id": entry["id"], "name": entry["name"], "side": entry["side"],
            "initiative": entry["initiative"], "current": entry["id"] == atual,
        }
        if fighter is not None:
            if entry["id"] == player_id:
                linha.update(hp=fighter.hp, hp_max=fighter.hp_max)
            linha["health"] = health_label(fighter.hp, fighter.hp_max)
        linhas.append(linha)
    return {
        "active": bool(encounter.get("active")),
        "round": int(encounter.get("round") or 1),
        "outcome": encounter.get("outcome"),
        "order": linhas,
    }
