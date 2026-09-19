"""Criação de personagem D&D 5e (imaginai/dnd5e_build.py) — regras do livro, dado injetável.

Origem: a IA "registrou" um Feiticeiro humano no texto, mas a ficha ficou com tudo 10,
nenhuma salvaguarda nem perícia proficiente e sem equipamento. Agora o servidor calcula.
"""
from __future__ import annotations

import pytest

from aiworkspace.imaginai import dnd5e_build as b


def _dado(*valores):
    seq = list(valores)
    return lambda sides: seq.pop(0)


_ARRAY = {"FOR": 8, "DES": 14, "CON": 13, "INT": 10, "SAB": 12, "CAR": 15}


def _feiticeiro(**extra):
    build = {"class": "Feiticeiro", "race": "Humano", "background": "Sábio",
             "method": "standard_array", "base_abilities": b.parse_scores(_ARRAY),
             "class_skills": ["Persuasão", "Intuição"]}
    build.update(extra)
    return build


# --------------------------------------------------------------------------- #
# Atributos                                                                    #
# --------------------------------------------------------------------------- #
def test_4d6_descarta_o_menor():
    assert b.roll_ability(_dado(1, 6, 5, 4)) == {"total": 15, "dice": [1, 6, 5, 4]}


def test_conjunto_padrao_exige_os_seis_valores_exatos():
    assert b.validate_abilities("standard_array", b.parse_scores(_ARRAY), None)
    with pytest.raises(b.BuildError, match="15, 14, 13, 12, 10 e 8"):
        b.validate_abilities("standard_array", b.parse_scores({**_ARRAY, "FOR": 15}), None)


def test_rolagem_so_aceita_os_valores_rolados():
    pool = [16, 14, 12, 11, 9, 7]
    ok = {"FOR": 7, "DES": 14, "CON": 12, "INT": 11, "SAB": 9, "CAR": 16}
    assert b.validate_abilities("roll", b.parse_scores(ok), pool)["charisma"] == 16
    with pytest.raises(b.BuildError, match="valores rolados"):
        b.validate_abilities("roll", b.parse_scores({**ok, "FOR": 18}), pool)


def test_compra_de_pontos_respeita_27_e_a_faixa_8_15():
    caro = {"FOR": 15, "DES": 15, "CON": 15, "INT": 8, "SAB": 8, "CAR": 8}   # 27 pontos
    assert b.validate_abilities("point_buy", b.parse_scores(caro), None)
    with pytest.raises(b.BuildError, match="gastou 28"):
        b.validate_abilities("point_buy", b.parse_scores({**caro, "INT": 9}), None)
    with pytest.raises(b.BuildError, match="8 a 15"):
        b.validate_abilities("point_buy", b.parse_scores({**caro, "FOR": 16}), None)


def test_atributo_faltando_e_apontado():
    with pytest.raises(b.BuildError, match="CAR"):
        b.validate_abilities("manual", {"strength": 10, "dexterity": 10, "constitution": 10,
                                        "intelligence": 10, "wisdom": 10}, None)


# --------------------------------------------------------------------------- #
# Ficha calculada                                                              #
# --------------------------------------------------------------------------- #
def test_feiticeiro_humano_sai_calculado_pelo_livro():
    sheet, missing, _ = b.derive(_feiticeiro())
    assert missing == []
    # humano: +1 em tudo
    assert sheet["attributes"] == {"strength": 9, "dexterity": 15, "constitution": 14,
                                   "intelligence": 11, "wisdom": 13, "charisma": 16}
    assert sheet["hp"] == {"current": 8, "max": 8}           # d6 cheio + CON(+2)
    assert sheet["armor_class"] == 12                         # sem armadura: 10 + DES(+2)
    assert sheet["saving_throws"]["constitution"]["proficient"]
    assert sheet["saving_throws"]["charisma"]["proficient"]
    assert not sheet["saving_throws"]["strength"]["proficient"]
    # sábio: arcanismo + história; classe: persuasão + intuição
    assert set(sheet["skills"]) == {"arcana", "history", "persuasion", "insight"}
    assert sheet["spellcasting"]["save_dc"] == 13             # 8 + 2 + CAR(+3)
    assert sheet["spell_save_dc"] == 13 and sheet["spell_attack_modifier"] == 5
    assert sheet["spell_slots"] == {"1": {"current": 2, "max": 2}}
    besta = next(a for a in sheet["attacks"] if a["name"] == "Besta leve")
    assert besta["attack_modifier"] == 4 and besta["damage"] == "1d8+2"


def test_armadura_e_escudo_do_guerreiro_entram_na_ca():
    sheet, _, _ = b.derive(_feiticeiro(**{"class": "Guerreiro", "class_skills": ["Atletismo", "Percepção"]}))
    assert sheet["armor_class"] == 18                          # cota de malha 16 + escudo 2
    assert sheet["passive_perception"] == 10 + 1 + 2           # SAB 13 (+1) + proficiente


def test_o_que_falta_e_listado_para_o_narrador_perguntar():
    _, missing, _ = b.derive({"class": "Ladino"})
    textos = " ".join(missing)
    assert "race" in textos and "background" in textos and "abilities" in textos
    assert "class_skills" in textos


def test_pericia_fora_da_lista_da_classe_e_recusada():
    with pytest.raises(b.BuildError, match="fora da lista"):
        b.derive(_feiticeiro(class_skills=["Furtividade", "Intuição"]))


def test_pericia_repetida_do_antecedente_pede_outra():
    with pytest.raises(b.BuildError, match="já vem do antecedente"):
        b.derive(_feiticeiro(class_skills=["Arcanismo", "Intuição"]))


def test_ancestralidade_da_campanha_usa_regra_flexivel():
    build = _feiticeiro(race="Marca-de-Deus")
    _, missing, _ = b.derive(build)
    assert any("bônus" in m for m in missing)
    sheet, missing, _ = b.derive({**build, "race_bonuses": {"CAR": 2, "CON": 1}})
    assert missing == []
    assert sheet["ancestry"] == "Marca-de-Deus"
    assert sheet["attributes"]["charisma"] == 17
    with pytest.raises(b.BuildError, match=r"\+2 e \+1"):
        b.derive({**build, "race_bonuses": {"CAR": 3}})


def test_antecedente_proprio_escolhe_duas_pericias():
    build = _feiticeiro(background="Coletor de água de chuva")
    _, missing, _ = b.derive(build)
    assert any("background_skills" in m for m in missing)
    sheet, missing, _ = b.derive({**build, "background_skills": ["Sobrevivência", "Percepção"]})
    assert missing == [] and {"survival", "perception"} <= set(sheet["skills"])


def test_pv_acima_do_nivel_1_rola_uma_vez_e_guarda():
    """Rolagem de PV fica gravada: recalcular a ficha não re-rola (sem pescar valor)."""
    build = _feiticeiro(level=3, hp_method="roll")
    sheet, _, build = b.derive(build, _dado(1, 6))
    assert build["hp_rolls"] == [1, 6]
    assert sheet["hp"]["max"] == 8 + max(1, 1 + 2) + (6 + 2)
    de_novo, _, _ = b.derive(build, _dado(6, 6))
    assert de_novo["hp"]["max"] == sheet["hp"]["max"]


def test_ladino_dobra_o_bonus_na_especializacao():
    build = {"class": "Ladino", "race": "Halfling", "background": "Criminoso",
             "method": "standard_array", "base_abilities": b.parse_scores(_ARRAY),
             "class_skills": ["Acrobacia", "Percepção", "Investigação", "Intuição"]}
    _, missing, _ = b.derive(build)
    assert any("expertise" in m for m in missing)
    sheet, missing, _ = b.derive({**build, "expertise": ["Furtividade", "Percepção"]})
    assert missing == []
    assert sheet["skills"]["stealth"]["proficiency"] == 2
    assert sheet["speed"] == 25


def test_magias_demais_no_nivel_1_sao_recusadas():
    truques = [{"name": f"Truque {i}", "level": 0} for i in range(5)]
    with pytest.raises(b.BuildError, match="até 4 truque"):
        b.derive(_feiticeiro(spells=truques))


def test_espacos_de_meio_conjurador_e_bruxo():
    assert b.spell_slots({"type": "half"}, 1) == {}
    assert b.spell_slots({"type": "half"}, 5) == {"1": {"current": 4, "max": 4}, "2": {"current": 2, "max": 2}}
    assert b.spell_slots({"type": "pact"}, 5) == {"3": {"current": 2, "max": 2}}


def test_equipamento_inicial_e_ouro_do_antecedente():
    itens, ouro = b.starting_equipment({"class": "Guerreiro", "background": "Nobre"})
    nomes = [i["name"] for i in itens]
    assert "Cota de malha" in nomes and "Escudo" in nomes and "Espada longa" in nomes
    assert next(i for i in itens if i["name"] == "Cota de malha")["equipped"]
    assert ouro == 25


@pytest.mark.parametrize("texto,chave", [
    ("sorcerer", "sorcerer"), ("FEITICEIRO", "sorcerer"), ("Clérigo", "cleric"), ("paladino", "paladin"),
])
def test_classe_aceita_nome_pt_en_e_acentos(texto, chave):
    assert b.find_class(texto)[0] == chave
