"""Magias completas, expansão do mundo e a recusa de texto corrompido.

- Magia de dano sem `roll_kind: spell_attack` não era resolvida pelo kernel ao
  conjurar; agora todo autor (criação, narrador, jogador) passa por spells.normalize.
- "contra��do" chegou do provedor do modelo e foi gravado na premissa: a ponte das
  ferramentas recusa U+FFFD e pede o reenvio.
"""
from __future__ import annotations

import pytest

from aiworkspace.imaginai import setup, spells
from aiworkspace.imaginai.turns import ImaginaiTurnBridge, _corrupted_text


def test_magia_de_dano_vira_ataque_magico_que_o_kernel_resolve():
    s = spells.normalize({"name": "Raio de Fogo", "level": 0, "damage": "1d10",
                          "damage_type": "fogo", "range": "36 m"}, attack_modifier=5)
    assert s["effect"] == {"roll_kind": "spell_attack", "damage": "1d10",
                           "damage_type": "fogo", "attack_modifier": 5}
    assert s["range"] == "36 m" and s["attack"] is True


def test_magia_de_salvaguarda_nao_vira_ataque():
    s = spells.normalize({"name": "Bola de Fogo", "level": 3, "damage": "8d6", "save": "DES"})
    assert s["attack"] is False and "effect" not in s


def test_cura_monta_efeito_de_cura():
    assert spells.normalize({"name": "Curar Ferimentos", "level": 1, "healing": 7})["effect"] == {"healing": 7}


def test_dados_invalidos_sao_descartados():
    assert spells.normalize({"name": "X", "damage": "muito"})["damage"] == ""


def test_componentes_em_texto_viram_lista():
    assert spells.normalize({"name": "X", "components": "V, S, M (cinza)"})["components"] == ["V", "S", "M (cinza)"]


def test_merge_atualiza_por_nome_sem_duplicar_e_remove():
    atual = [{"name": "Luz", "level": 0}, {"name": "Escudo", "level": 1}]
    novo = spells.merge(atual, [{"name": "luz", "level": 0, "description": "Brilha."},
                                {"name": "Mísseis Mágicos", "level": 1}], 4, remove=["Escudo"])
    assert [s["name"] for s in novo] == ["luz", "Mísseis Mágicos"]
    assert novo[0]["description"] == "Brilha."


def test_sem_nome_nao_entra():
    assert spells.normalize({"level": 1}) is None


# --------------------------------------------------------------------------- #
# Expansão (regras puras)                                                      #
# --------------------------------------------------------------------------- #
def test_expansao_liga_local_novo_a_local_antigo():
    mundo = setup.validate_world(
        {"locations": [{"name": "Forja dos Uivos", "connections": ["Vilagris"]}],
         "npcs": [{"name": "Ferreiro Surdo", "location": "Vilagris"}]},
        existing={"vilagris": "Vilagris"},
    )
    assert ("Forja dos Uivos", "Vilagris") in mundo["edges"]
    assert mundo["npcs"][0]["location"] == "Vilagris"


def test_expansao_so_de_caminhos_entre_locais_antigos():
    """O mapa antigo sem linhas: repetir locais existentes só com connections."""
    mundo = setup.validate_world(
        {"locations": [{"name": "Vilagris", "connections": ["Catedral do Osso"]}]},
        existing={"vilagris": "Vilagris", "catedral do osso": "Catedral do Osso"},
    )
    assert mundo["edges"] == [("Catedral do Osso", "Vilagris")]


def test_expansao_pode_vir_sem_local_novo():
    assert setup.validate_world({"lore": ["Os sinos tocam sozinhos."]}, existing={})["lore"]


def test_mundo_novo_continua_exigindo_local():
    with pytest.raises(setup.SetupError):
        setup.validate_world({"lore": ["x"]})


# --------------------------------------------------------------------------- #
# Texto corrompido                                                             #
# --------------------------------------------------------------------------- #
def test_detecta_ufffd_em_qualquer_profundidade():
    args = {"action": "set_concept", "concept": {"premise": "lembra ter contra��do"}}
    assert _corrupted_text(args) == "concept.premise"
    assert _corrupted_text({"world": {"npcs": [{"name": "ok"}, {"name": "b�"}]}}) == "world.npcs[1].name"
    assert _corrupted_text({"concept": {"premise": "contraído"}}) is None


async def test_ponte_recusa_texto_corrompido_sem_tocar_no_banco():
    import uuid

    bridge = ImaginaiTurnBridge(user_id=uuid.uuid4(), campaign_id=uuid.uuid4(), turn_key="t")
    out = await bridge.run("imaginai_setup", {"action": "set_concept",
                                              "concept": {"premise": "contra��do"}})
    assert "Reenvie" in out["error"] and "concept.premise" in out["error"]
