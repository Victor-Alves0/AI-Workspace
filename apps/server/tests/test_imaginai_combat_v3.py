"""Combate v3: habilidades com recarga, tipos de dano e ataque de oportunidade."""
from __future__ import annotations

from aiworkspace.imaginai import combat
from aiworkspace.imaginai.combat import Fighter


def _dado(*valores: int):
    fila = list(valores)
    return lambda _lados: fila.pop(0)


def _pj(**kw) -> Fighter:
    base = dict(id="pj", name="Herói", side="player", hp=40, hp_max=40, ac=15,
                location_id="t", saves={"dexterity": 0})
    base.update(kw)
    return Fighter(**base)


def _enc(*ids):
    return {"active": True, "round": 1, "turn": 0, "outcome": None,
            "order": [{"id": i, "name": i, "side": "x", "initiative": 20 - n, "bonus": 0}
                      for n, i in enumerate(ids)]}


_MORDIDA = {"type": "attack", "name": "Mordida", "attack_modifier": 5, "damage": "1d6", "damage_type": "piercing"}
_BAFORADA = {"type": "save", "name": "Baforada", "save": "dexterity", "dc": 30, "damage": "2d6",
             "damage_type": "fire", "half": True, "recharge": 5}


def _dragao(**kw) -> Fighter:
    base = dict(id="dr", name="Dragão", side="hostile", hp=50, hp_max=50, ac=15, location_id="t",
                actions=(_MORDIDA, _BAFORADA))
    base.update(kw)
    return Fighter(**base)


# --------------------------------------------------------------------------- #
# Recarga                                                                      #
# --------------------------------------------------------------------------- #
def test_habilidade_com_recarga_e_usada_primeiro_e_fica_gasta():
    # rodada 1: baforada (resistência d20=1 falha; dano 2d6 = 3+4)
    rel = combat.run_enemy_turns(_enc("dr", "pj"), {"pj": _pj(), "dr": _dragao()}, "pj", _dado(1, 3, 4))
    assert rel.enemy_turns[0]["attack_name"] == "Baforada" and rel.enemy_turns[0]["damage"] == 7
    assert rel.encounter["recharge_spent"]["dr"] == ["Baforada"]


def test_recarga_que_falha_usa_ataque_comum():
    enc = {**_enc("dr", "pj"), "recharge_spent": {"dr": ["Baforada"]}, "round": 2}
    # d6 de recarga = 3 (< 5): não volta; ataque comum: d20=10 +5 = 15 acerta; 1d6 = 2
    rel = combat.run_enemy_turns(enc, {"pj": _pj(), "dr": _dragao()}, "pj", _dado(3, 10, 2))
    assert rel.enemy_turns[0]["attack_name"] == "Mordida"
    assert rel.encounter["recharge_spent"]["dr"] == ["Baforada"]


def test_recarga_que_sai_5_ou_6_volta_na_hora():
    enc = {**_enc("dr", "pj"), "recharge_spent": {"dr": ["Baforada"]}, "round": 2}
    rel = combat.run_enemy_turns(enc, {"pj": _pj(), "dr": _dragao()}, "pj", _dado(6, 1, 2, 2))
    turno = rel.enemy_turns[0]
    assert turno["attack_name"] == "Baforada" and turno["recharged"] == ["Baforada"]


# --------------------------------------------------------------------------- #
# Tipos de dano                                                                #
# --------------------------------------------------------------------------- #
def test_resistente_toma_metade():
    pj = _pj(traits={"resistances": frozenset({"fire"})})
    rel = combat.run_enemy_turns(_enc("dr", "pj"), {"pj": pj, "dr": _dragao()}, "pj", _dado(1, 3, 4))
    turno = rel.enemy_turns[0]
    assert turno["damage"] == 3 and turno["damage_before_traits"] == 7 and turno["damage_note"] == "resistente"


def test_imune_nao_toma_nada():
    pj = _pj(traits={"immunities": frozenset({"fire"})})
    rel = combat.run_enemy_turns(_enc("dr", "pj"), {"pj": pj, "dr": _dragao()}, "pj", _dado(1, 3, 4))
    assert rel.enemy_turns[0]["damage"] == 0 and rel.fighters["pj"].hp == 40


def test_vulneravel_toma_o_dobro():
    assert combat.apply_damage_traits(7, "fogo", {"vulnerabilities": frozenset({"fire"})}) == (14, "vulnerável")


def test_tipo_de_dano_em_portugues_e_lido():
    traits = combat.damage_traits({"dnd5e": {"resistances": ["Fogo", "cortante", "xyz"]}})
    assert traits["resistances"] == frozenset({"fire", "slashing"})


# --------------------------------------------------------------------------- #
# Ataque de oportunidade                                                       #
# --------------------------------------------------------------------------- #
def test_fugir_provoca_um_ataque_de_cada_hostil_do_local():
    gob = Fighter(id="g", name="Goblin", side="hostile", hp=7, hp_max=7, ac=13, location_id="t",
                  attack={"name": "Cimitarra", "attack_modifier": 4, "damage": "1d6+2"})
    longe = Fighter(id="l", name="Arqueiro", side="hostile", hp=7, hp_max=7, ac=13, location_id="outro")
    turnos, pool = combat.opportunity_attacks({"pj": _pj(), "g": gob, "l": longe}, "pj", "t", _dado(15, 4))
    assert len(turnos) == 1 and turnos[0]["kind"] == "opportunity" and turnos[0]["attacker"] == "Goblin"
    assert pool["pj"].hp == 34


def test_hostil_atordoado_nao_tem_reacao():
    gob = Fighter(id="g", name="Goblin", side="hostile", hp=7, hp_max=7, ac=13, location_id="t",
                  conditions=({"key": "stunned", "rounds": 1},))
    turnos, _ = combat.opportunity_attacks({"pj": _pj(), "g": gob}, "pj", "t", _dado())
    assert turnos == []
