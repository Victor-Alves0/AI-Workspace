"""Combate v2: aliados, habilidades com teste de resistência e condições.

Pedido do Victor (19/09): aliados e NPCs lutando junto, condições, magia de inimigos,
testes de resistência. Tudo decidido pelo servidor (combat.py) e testável com dado fixo.
"""
from __future__ import annotations

from aiworkspace.imaginai import combat
from aiworkspace.imaginai.combat import Fighter


def _dado(*valores: int):
    fila = list(valores)
    return lambda _lados: fila.pop(0)


def _pj(**kw) -> Fighter:
    base = dict(id="pj", name="Herói", side="player", hp=20, hp_max=20, ac=15,
                location_id="t", saves={"dexterity": 2})
    base.update(kw)
    return Fighter(**base)


def _gob(fid="gob", **kw) -> Fighter:
    base = dict(id=fid, name="Goblin", side="hostile", hp=7, hp_max=7, ac=13, location_id="t",
                attack={"name": "Cimitarra", "attack_modifier": 4, "damage": "1d6+2"})
    base.update(kw)
    return Fighter(**base)


def _enc(*ids, turn=0):
    return {"active": True, "round": 1, "turn": turn, "outcome": None,
            "order": [{"id": i, "name": i, "side": "x", "initiative": 20 - n, "bonus": 0}
                      for n, i in enumerate(ids)]}


# --------------------------------------------------------------------------- #
# Aliados                                                                      #
# --------------------------------------------------------------------------- #
def test_aliado_ataca_o_inimigo_no_turno_dele():
    aliado = Fighter(id="al", name="Gromm", side="ally", hp=12, hp_max=12, ac=12, location_id="t",
                     attack={"name": "Machado", "attack_modifier": 5, "damage": "1d8+3"})
    lut = {"pj": _pj(), "gob": _gob(), "al": aliado}
    # aliado: d20=15 (+5=20 ≥ 13) acerta, 1d8=4 +3 = 7 → goblin cai → vitória
    rel = combat.run_enemy_turns(_enc("al", "gob", "pj"), lut, "pj", _dado(15, 4))
    assert rel.enemy_turns[0]["attacker_side"] == "ally" and rel.enemy_turns[0]["damage"] == 7
    assert rel.fighters["gob"].down
    assert rel.encounter["outcome"] == "victory"


def test_inimigo_pode_mirar_o_aliado():
    aliado = Fighter(id="al", name="Gromm", side="ally", hp=12, hp_max=12, ac=10, location_id="t")
    lut = {"pj": _pj(), "gob": _gob(), "al": aliado}
    # 2 alvos possíveis: dado de escolha = 2 → o segundo da lista (o aliado)
    rel = combat.run_enemy_turns(_enc("gob", "pj"), lut, "pj", _dado(2, 12, 4))
    turno = rel.enemy_turns[0]
    assert turno["target_id"] == "al" and turno["hit"] and rel.fighters["al"].hp == 6


def test_aliado_caido_nao_age():
    aliado = Fighter(id="al", name="Gromm", side="ally", hp=0, hp_max=12, ac=12, location_id="t")
    rel = combat.run_enemy_turns(_enc("al", "pj"), {"pj": _pj(), "gob": _gob(), "al": aliado}, "pj", _dado())
    assert rel.enemy_turns == []


# --------------------------------------------------------------------------- #
# Habilidade com teste de resistência                                         #
# --------------------------------------------------------------------------- #
_BAFORADA = {"type": "save", "name": "Baforada", "save": "dexterity", "dc": 13,
             "damage": "4d6", "half": True, "condition": "prone", "condition_rounds": 1}


def test_falhou_na_resistencia_leva_tudo_e_a_condicao():
    dragao = _gob("dr", actions=(_BAFORADA,))
    # resistência: d20=5 +2 = 7 < 13 → falhou; dano 4d6 = 3+3+3+3 = 12
    rel = combat.run_enemy_turns(_enc("dr", "pj"), {"pj": _pj(), "dr": dragao}, "pj", _dado(5, 3, 3, 3, 3))
    turno = rel.enemy_turns[0]
    assert turno["kind"] == "save" and turno["saved"] is False and turno["damage"] == 12
    assert turno["condition_applied"] == "prone"
    assert {"key": "prone", "rounds": 1} in rel.fighters["pj"].conditions


def test_passou_na_resistencia_leva_metade_e_sem_condicao():
    dragao = _gob("dr", actions=(_BAFORADA,))
    rel = combat.run_enemy_turns(_enc("dr", "pj"), {"pj": _pj(), "dr": dragao}, "pj", _dado(15, 3, 3, 3, 3))
    turno = rel.enemy_turns[0]
    assert turno["saved"] is True and turno["damage"] == 6
    assert rel.fighters["pj"].conditions == ()


def test_criatura_alterna_as_acoes_por_rodada():
    mordida = {"type": "attack", "name": "Mordida", "attack_modifier": 4, "damage": "1d6"}
    dragao = _gob("dr", actions=(mordida, _BAFORADA))
    rel = combat.run_enemy_turns(_enc("dr", "pj"), {"pj": _pj(), "dr": dragao}, "pj", _dado(1))
    assert rel.enemy_turns[0]["attack_name"] == "Mordida"          # rodada 1
    enc2 = {**rel.encounter, "turn": 0, "round": 2}
    rel2 = combat.run_enemy_turns(enc2, rel.fighters, "pj", _dado(20, 1, 1, 1, 1))
    assert rel2.enemy_turns[0]["attack_name"] == "Baforada"        # rodada 2


# --------------------------------------------------------------------------- #
# Condições                                                                    #
# --------------------------------------------------------------------------- #
def test_alvo_caido_da_vantagem_ao_atacante():
    pj = _pj(conditions=({"key": "prone", "rounds": 1},))
    # vantagem: rola 2 d20 (3 e 16), fica o maior → 16+4=20 acerta
    rel = combat.run_enemy_turns(_enc("gob", "pj"), {"pj": pj, "gob": _gob()}, "pj", _dado(3, 16, 4))
    turno = rel.enemy_turns[0]
    assert turno["mode"] == "advantage" and turno["rolls"] == [3, 16] and turno["hit"]


def test_atacante_envenenado_tem_desvantagem():
    gob = _gob(conditions=({"key": "poisoned", "rounds": 3},))
    rel = combat.run_enemy_turns(_enc("gob", "pj"), {"pj": _pj(), "gob": gob}, "pj", _dado(18, 2))
    assert rel.enemy_turns[0]["mode"] == "disadvantage" and rel.enemy_turns[0]["hit"] is False


def test_vantagem_e_desvantagem_se_anulam():
    pj = _pj(conditions=({"key": "prone", "rounds": 1},))
    gob = _gob(conditions=({"key": "poisoned", "rounds": 3},))
    rel = combat.run_enemy_turns(_enc("gob", "pj"), {"pj": pj, "gob": gob}, "pj", _dado(12, 4))
    assert rel.enemy_turns[0]["mode"] == "normal" and rel.enemy_turns[0]["rolls"] == [12]


def test_atordoado_perde_o_turno_e_a_duracao_cai():
    gob = _gob(conditions=({"key": "stunned", "rounds": 1},))
    rel = combat.run_enemy_turns(_enc("gob", "pj"), {"pj": _pj(), "gob": gob}, "pj", _dado())
    assert rel.enemy_turns[0]["kind"] == "skipped"
    assert rel.fighters["gob"].conditions == ()                   # 1 rodada: acabou


def test_paralisado_falha_resistencia_de_destreza_sozinho():
    pj = _pj(conditions=({"key": "paralyzed", "rounds": None},))
    save = combat.roll_save(pj, "dexterity", 10, _dado())
    assert save["success"] is False and save["auto_fail"] is True


def test_acerto_em_inconsciente_e_critico():
    pj = _pj(conditions=({"key": "unconscious", "rounds": None},), hp=30, hp_max=30)
    # vantagem (inconsciente): 2 d20 → 10, 12 → 12+4=16 ≥ 15 acerta → crítico (dobra dados)
    rel = combat.run_enemy_turns(_enc("gob", "pj"), {"pj": pj, "gob": _gob()}, "pj", _dado(10, 12, 3, 3))
    turno = rel.enemy_turns[0]
    assert turno["critical"] is True and turno["damage_rolls"] == [3, 3] and turno["damage"] == 8


def test_quem_cai_fica_inconsciente():
    pj = _pj(hp=3)
    rel = combat.run_enemy_turns(_enc("gob", "pj"), {"pj": pj, "gob": _gob()}, "pj", _dado(15, 4))
    assert rel.fighters["pj"].down
    assert any(c["key"] == "unconscious" for c in rel.fighters["pj"].conditions)
    assert rel.encounter["outcome"] == "defeat"


def test_condicao_por_nome_em_portugues():
    assert combat.condition_key("Envenenado") == "poisoned"
    assert combat.condition_key("caído") == "prone"
    assert combat.condition_key("xyz") is None


def test_visao_publica_mostra_condicoes_e_vida_do_aliado():
    aliado = Fighter(id="al", name="Gromm", side="ally", hp=5, hp_max=12, ac=12, location_id="t",
                     conditions=({"key": "poisoned", "rounds": 2},))
    enc = {"active": True, "round": 1, "turn": 0, "outcome": None,
           "order": [{"id": "al", "name": "Gromm", "side": "ally", "initiative": 10}]}
    linha = combat.public_view(enc, {"al": aliado}, "pj")["order"][0]
    assert linha["hp"] == 5 and linha["side"] == "ally"
    assert linha["conditions"][0]["label"] == "envenenado"


def test_bonus_de_resistencia_usa_proficiencia_da_ficha():
    state = {"dnd5e": {"attributes": {"dexterity": 14}, "proficiency_bonus": 2,
                       "saving_throws": {"dexterity": {"proficient": True}}}}
    assert combat.save_modifiers(state)["dexterity"] == 4
    assert combat.save_modifiers(state)["strength"] == 0
