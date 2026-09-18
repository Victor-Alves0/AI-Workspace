"""Sessão zero do Imaginai — as regras que decidem se dá para avançar de etapa.

A campanha passa por conceito → personagem → jogo, e cada turno recebe só a
instrução da etapa atual. Estes testes travam o que decide a passagem: quando o
conceito está completo, o que é um mundo aceitável (o modelo escreve livre; o banco
não aceita qualquer coisa) e quando o personagem está pronto para entrar em cena.
"""
from __future__ import annotations

import uuid

import pytest

from aiworkspace.imaginai import setup
from aiworkspace.models import ImaginaiCampaign, ImaginaiEntity


def _campanha(**kw) -> ImaginaiCampaign:
    base = dict(id=uuid.uuid4(), user_id=uuid.uuid4(), chat_id=uuid.uuid4(),
                name="Nome da Campanha", settings={}, setup_stage="concept")
    base.update(kw)
    return ImaginaiCampaign(**base)


def _jogador(name="Nome do personagem", classe="Classe") -> ImaginaiEntity:
    return ImaginaiEntity(id=uuid.uuid4(), campaign_id=uuid.uuid4(), user_id=uuid.uuid4(),
                          kind="character", key="player", name=name,
                          state={"dnd5e": {"class": classe, "level": 1}})


# --------------------------------------------------------------------------- #
# Conceito                                                                     #
# --------------------------------------------------------------------------- #
def test_campanha_recem_criada_tem_o_conceito_inteiro_por_fazer():
    """O nome-placeholder "Nome da Campanha" não conta como nome escolhido."""
    faltando = setup.missing_concept(setup.concept_of(_campanha()))
    assert faltando == ["name", "genre", "premise"]


def test_conceito_completo_com_nome_genero_e_premissa():
    c = _campanha(name="As Cinzas de Varn",
                  settings={"genre": "fantasia sombria", "premise": "Um reino que queima em silêncio."})
    assert setup.missing_concept(setup.concept_of(c)) == []


def test_tema_e_tom_enriquecem_mas_nao_bloqueiam():
    """O narrador pode propor tema e tom sozinho; travar a criação por eles só
    atrasaria o jogador."""
    c = _campanha(name="X", settings={"genre": "horror", "premise": "p"})
    assert "theme" not in setup.missing_concept(setup.concept_of(c))
    assert "tone" not in setup.missing_concept(setup.concept_of(c))


# --------------------------------------------------------------------------- #
# Mundo                                                                        #
# --------------------------------------------------------------------------- #
def test_mundo_sem_local_e_recusado_com_o_motivo():
    with pytest.raises(setup.SetupError, match="ao menos um local"):
        setup.validate_world({"npcs": [{"name": "Aria"}]})


def test_local_inicial_invalido_cai_no_primeiro_local():
    mundo = setup.validate_world({
        "locations": [{"name": "Porto Cinza"}, {"name": "Torre"}],
        "starting_location": "Lugar que não existe",
    })
    assert mundo["starting_location"] == "Porto Cinza"


def test_npc_no_local_inicial_e_visivel_e_o_resto_se_descobre():
    """Quem está onde o jogador começa é visto de cara; o vilão no castelo distante,
    não — ele tem de ser descoberto jogando."""
    mundo = setup.validate_world({
        "locations": [{"name": "Taverna"}, {"name": "Castelo"}],
        "starting_location": "Taverna",
        "npcs": [
            {"name": "Taberneira", "location": "Taverna"},
            {"name": "Lorde Sombrio", "location": "Castelo"},
        ],
    })
    vis = {n["name"]: n["visibility"] for n in mundo["npcs"]}
    assert vis == {"Taberneira": "known", "Lorde Sombrio": "hidden"}


def test_npc_apontando_para_local_inexistente_fica_sem_local():
    mundo = setup.validate_world({
        "locations": [{"name": "Taverna"}],
        "npcs": [{"name": "Andarilho", "location": "Algum lugar"}],
    })
    assert mundo["npcs"][0]["location"] is None


def test_criatura_hostil_recebe_estatisticas_e_dano_invalido_e_descartado():
    mundo = setup.validate_world({
        "locations": [{"name": "Floresta"}],
        "npcs": [
            {"name": "Lobo", "kind": "creature", "hostile": True, "hp": 11, "ac": 13,
             "attack_bonus": 4, "damage": "2d4+2"},
            {"name": "Espectro", "kind": "creature", "hostile": True, "damage": "muito"},
        ],
    })
    lobo, espectro = mundo["npcs"]
    assert lobo["hostile"] and lobo["hp"] == 11 and lobo["damage"] == "2d4+2"
    assert espectro["damage"] == ""           # cai no ataque padrão do combate
    assert espectro["hp"] == 12               # padrão de criatura


def test_persona_do_npc_e_preservada_para_o_mestre():
    mundo = setup.validate_world({
        "locations": [{"name": "Taverna"}],
        "npcs": [{"name": "Taberneira", "persona": "Espiã da facção rival."}],
    })
    assert mundo["npcs"][0]["persona"] == "Espiã da facção rival."


def test_tetos_de_quantidade_seguram_mundos_gigantes():
    mundo = setup.validate_world({
        "locations": [{"name": f"L{i}"} for i in range(50)],
        "npcs": [{"name": f"N{i}"} for i in range(80)],
        "lore": [f"verdade {i}" for i in range(100)],
    })
    assert len(mundo["locations"]) == setup.MAX_LOCATIONS
    assert len(mundo["npcs"]) == setup.MAX_NPCS
    assert len(mundo["lore"]) == setup.MAX_LORE


def test_chaves_de_entidade_sao_unicas_mesmo_com_nomes_repetidos():
    taken: set[str] = set()
    a = setup.slug("Guarda da Cidade", taken)
    b = setup.slug("Guarda da Cidade", taken)
    assert a == "guarda-da-cidade" and b != a and b.startswith("guarda-da-cidade-")


# --------------------------------------------------------------------------- #
# Personagem e etapas                                                          #
# --------------------------------------------------------------------------- #
def test_personagem_com_placeholders_nao_pode_comecar():
    assert setup.missing_character(_jogador()) == ["name", "class"]


def test_personagem_com_nome_e_classe_esta_pronto():
    assert setup.missing_character(_jogador("Kael", "Ladino")) == []


def test_acao_fora_da_etapa_e_recusada_com_orientacao():
    """Criar o mundo de novo depois de pronto duplicaria tudo — a etapa impede."""
    with pytest.raises(setup.SetupError, match="criação do personagem"):
        setup._require(_campanha(setup_stage="character"), "concept")
