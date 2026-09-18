"""Combate por turnos do Imaginai — o núcleo mecânico.

Antes, o combate era unilateral: o jogador atacava, mas nenhum inimigo revidava, e a
flag `hostile` que o construtor de mundo gravava não era lida por nada. O narrador podia
descrever o goblin acertando, só que o HP do personagem não mudava.

Estes testes travam as regras que agora são do SERVIDOR: iniciativa, ordem de turnos,
ataque inimigo contra a CA do jogador, crítico/erro natural e o fim do combate. O dado
é injetado, então cada teste sabe exatamente o que vai sair.
"""
from __future__ import annotations

from aiworkspace.imaginai import combat
from aiworkspace.imaginai.combat import Fighter


def _dado(*valores: int):
    """Roller determinístico: devolve os valores na ordem (d20 e dados de dano)."""
    fila = list(valores)

    def roll(_lados: int) -> int:
        return fila.pop(0)
    return roll


def _jogador(**kw) -> Fighter:
    base = dict(id="pj", name="Herói", side="player", hp=20, hp_max=20, ac=15,
                location_id="taverna", initiative_bonus=2)
    base.update(kw)
    return Fighter(**base)


def _goblin(fid="gob", **kw) -> Fighter:
    base = dict(id=fid, name="Goblin", side="hostile", hp=7, hp_max=7, ac=13,
                location_id="taverna", initiative_bonus=2,
                attack={"name": "Cimitarra", "attack_modifier": 4, "damage": "1d6+2"})
    base.update(kw)
    return Fighter(**base)


# --------------------------------------------------------------------------- #
# Leitura do estado da entidade                                                #
# --------------------------------------------------------------------------- #
def test_hostilidade_e_lida_de_onde_o_construtor_grava():
    """O construtor de mundo grava `state.hostile = true` — e nada lia isso."""
    assert combat.is_hostile({"hostile": True})
    assert combat.is_hostile({"dnd5e": {"hostile": True}})
    assert not combat.is_hostile({"dnd5e": {"hp": {"max": 7}}})


def test_sem_hp_cadastrado_nao_entra_em_combate():
    """Um NPC sem estatísticas não tem como levar dano: fica fora do encontro em vez
    de virar um combatente imortal."""
    assert combat.fighter_from("x", "Aldeão", {"description": "só lore"}, "taverna", "hostile") is None
    lutador = combat.fighter_from("g", "Goblin", {"dnd5e": {"hp": {"current": 5, "max": 7}}}, "taverna", "hostile")
    assert lutador is not None and lutador.hp == 5 and lutador.hp_max == 7


def test_criatura_sem_ataque_cadastrado_usa_o_padrao():
    perfil = combat.attack_profile({"dnd5e": {"hp": {"max": 7}}})
    assert perfil == combat.DEFAULT_ATTACK


def test_ataque_cadastrado_tem_precedencia_sobre_o_padrao():
    perfil = combat.attack_profile({"dnd5e": {"attacks": [
        {"key": "bite", "name": "Mordida", "attack_modifier": 5, "damage": "2d4+3"},
    ]}})
    assert perfil["name"] == "Mordida" and perfil["damage"] == "2d4+3" and perfil["attack_modifier"] == 5


def test_iniciativa_usa_a_destreza():
    assert combat.initiative_bonus({"dnd5e": {"attributes": {"dexterity": 14}}}) == 2
    assert combat.initiative_bonus({"dnd5e": {"attributes": {"dexterity": {"score": 8}}}}) == -1
    assert combat.initiative_bonus({}) == 0


# --------------------------------------------------------------------------- #
# Iniciativa e início                                                          #
# --------------------------------------------------------------------------- #
def test_ordem_de_iniciativa_do_maior_para_o_menor():
    ordem = combat.roll_initiative([_jogador(), _goblin()], _dado(5, 18))
    assert [r["id"] for r in ordem] == ["gob", "pj"]
    assert ordem[0]["initiative"] == 20      # 18 + 2


def test_empate_favorece_o_jogador():
    ordem = combat.roll_initiative([_goblin(), _jogador()], _dado(10, 10))
    assert ordem[0]["id"] == "pj"


def test_combate_iniciado_pelo_ataque_do_jogador_conta_esse_ataque_como_o_turno_dele():
    """O jogador atacou primeiro: não pode atacar DE NOVO antes de o goblin reagir."""
    enc = combat.start([_jogador(), _goblin()], "pj", _dado(18, 5), player_already_acted=True)
    assert enc["order"][0]["id"] == "pj"
    assert enc["turn"] == 1                  # ponteiro já está no goblin


def test_emboscada_comeca_do_topo_da_ordem():
    enc = combat.start([_jogador(), _goblin()], "pj", _dado(5, 18), player_already_acted=False)
    assert enc["turn"] == 0 and enc["order"][0]["id"] == "gob"


# --------------------------------------------------------------------------- #
# Turnos inimigos                                                              #
# --------------------------------------------------------------------------- #
def _encontro(*ids: str, turn: int = 0) -> dict:
    return {"active": True, "round": 1, "turn": turn, "outcome": None,
            "order": [{"id": i, "name": i, "side": "player" if i == "pj" else "hostile",
                       "initiative": 20 - n, "bonus": 0} for n, i in enumerate(ids)]}


def test_inimigo_acerta_e_o_dano_cai_no_hp_do_jogador():
    """O coração da correção: o dano no personagem é do SERVIDOR, não da narração."""
    lutadores = {"pj": _jogador(), "gob": _goblin()}
    # goblin: d20=12 (+4 = 16 ≥ CA 15) acerta; dano 1d6=4 (+2) = 6
    rel = combat.run_enemy_turns(_encontro("gob", "pj"), lutadores, "pj", _dado(12, 4))

    assert len(rel.enemy_turns) == 1
    turno = rel.enemy_turns[0]
    assert turno["hit"] is True and turno["damage"] == 6
    assert rel.fighters["pj"].hp == 14
    assert turno["target_hp"] == 14
    assert rel.encounter["turn"] == 1        # parou na vez do jogador


def test_inimigo_erra_abaixo_da_ca():
    lutadores = {"pj": _jogador(), "gob": _goblin()}
    rel = combat.run_enemy_turns(_encontro("gob", "pj"), lutadores, "pj", _dado(9))   # 9+4=13 < 15
    assert rel.enemy_turns[0]["hit"] is False
    assert rel.fighters["pj"].hp == 20


def test_vinte_natural_e_critico_e_dobra_os_dados_nao_o_modificador():
    lutadores = {"pj": _jogador(ac=30), "gob": _goblin()}
    # 20 natural acerta mesmo contra CA 30; crítico = 2d6 (3+5) + 2 = 10
    rel = combat.run_enemy_turns(_encontro("gob", "pj"), lutadores, "pj", _dado(20, 3, 5))
    turno = rel.enemy_turns[0]
    assert turno["critical"] is True and turno["hit"] is True
    assert turno["damage_rolls"] == [3, 5] and turno["damage"] == 10


def test_um_natural_erra_sempre():
    lutadores = {"pj": _jogador(ac=1), "gob": _goblin(attack={"attack_modifier": 50, "damage": "1d6"})}
    rel = combat.run_enemy_turns(_encontro("gob", "pj"), lutadores, "pj", _dado(1))
    assert rel.enemy_turns[0]["hit"] is False


def test_varios_inimigos_agem_em_ordem_e_a_rodada_avanca():
    """Ponteiro depois do jogador: os dois goblins agem, a rodada vira e para no PJ."""
    lutadores = {"pj": _jogador(), "g1": _goblin("g1"), "g2": _goblin("g2")}
    enc = _encontro("pj", "g1", "g2", turn=1)
    rel = combat.run_enemy_turns(enc, lutadores, "pj", _dado(2, 2))   # os dois erram

    assert [t["attacker_id"] for t in rel.enemy_turns] == ["g1", "g2"]
    assert rel.encounter["round"] == 2
    assert rel.encounter["turn"] == 0
    assert rel.encounter["active"] is True


def test_inimigo_caido_nao_age():
    lutadores = {"pj": _jogador(), "g1": _goblin("g1", hp=0), "g2": _goblin("g2")}
    rel = combat.run_enemy_turns(_encontro("g1", "g2", "pj"), lutadores, "pj", _dado(2))
    assert [t["attacker_id"] for t in rel.enemy_turns] == ["g2"]


# --------------------------------------------------------------------------- #
# Fim do combate                                                               #
# --------------------------------------------------------------------------- #
def test_todos_os_inimigos_caidos_e_vitoria():
    lutadores = {"pj": _jogador(), "gob": _goblin(hp=0)}
    rel = combat.run_enemy_turns(_encontro("gob", "pj"), lutadores, "pj", _dado())
    assert rel.encounter["active"] is False and rel.encounter["outcome"] == "victory"
    assert rel.enemy_turns == []


def test_jogador_a_zero_e_derrota_e_ninguem_bate_depois():
    """Jogador caído: o combate acaba ali — o segundo goblin não golpeia um corpo."""
    lutadores = {"pj": _jogador(hp=3), "g1": _goblin("g1"), "g2": _goblin("g2")}
    rel = combat.run_enemy_turns(_encontro("g1", "g2", "pj"), lutadores, "pj", _dado(15, 6))  # 6+2=8 ≥ 3
    assert rel.fighters["pj"].hp == 0
    assert rel.encounter["outcome"] == "defeat"
    assert len(rel.enemy_turns) == 1


def test_sair_do_local_encerra_o_combate_como_fuga():
    """Fugir pela porta: os inimigos ficam onde estavam e não alcançam mais o jogador."""
    lutadores = {"pj": _jogador(location_id="rua"), "gob": _goblin(location_id="taverna")}
    rel = combat.run_enemy_turns(_encontro("gob", "pj"), lutadores, "pj", _dado())
    assert rel.encounter["outcome"] == "escaped"
    assert rel.enemy_turns == []


def test_ordem_corrompida_nunca_vira_laco_infinito():
    """Sem o jogador na ordem, "a vez voltou ao jogador" nunca aconteceria."""
    lutadores = {"pj": _jogador(), "gob": _goblin()}
    enc = {"active": True, "round": 1, "turn": 0, "outcome": None,
           "order": [{"id": "gob", "name": "g", "side": "hostile", "initiative": 10, "bonus": 0}]}
    rel = combat.run_enemy_turns(enc, lutadores, "pj", _dado(*([2] * 20)))
    assert rel.encounter["active"] is False


# --------------------------------------------------------------------------- #
# O que o jogador vê                                                           #
# --------------------------------------------------------------------------- #
def test_jogador_ve_a_propria_vida_em_numeros_e_a_do_inimigo_como_estado():
    """No 5e o mestre não diz o HP exato do inimigo — diz como ele parece estar."""
    lutadores = {"pj": _jogador(hp=12), "gob": _goblin(hp=3)}
    vista = combat.public_view(_encontro("pj", "gob"), lutadores, "pj")

    pj = next(l for l in vista["order"] if l["id"] == "pj")
    gob = next(l for l in vista["order"] if l["id"] == "gob")
    assert pj["hp"] == 12 and pj["hp_max"] == 20
    assert "hp" not in gob
    assert gob["health"] == "gravemente ferido"
    assert pj["current"] is True


def test_rotulos_de_vida():
    assert combat.health_label(7, 7) == "ileso"
    assert combat.health_label(5, 7) == "ferido"
    assert combat.health_label(2, 7) == "gravemente ferido"
    assert combat.health_label(0, 7) == "caído"


def test_dano_invalido_nao_derruba_o_turno():
    """Uma expressão de dano mal cadastrada vira o dano padrão, não uma exceção no meio
    do combate."""
    dano, dados = combat.roll_damage("banana", _dado(4))
    assert dano == 5 and dados == [4]        # padrão 1d6+1


# --------------------------------------------------------------------------- #
# Fim do turno do jogador                                                      #
# --------------------------------------------------------------------------- #
def test_fim_do_turno_do_jogador_passa_a_vez_ao_proximo():
    enc = _encontro("gob", "pj", "g2", turn=1)
    assert combat.end_player_turn(enc, "pj")["turn"] == 2


def test_fim_de_turno_fora_da_vez_do_jogador_nao_pula_um_inimigo():
    """Se o ponteiro não está no jogador (reprocessamento, estado antigo), avançar às
    cegas faria um inimigo perder o turno sem ninguém perceber."""
    enc = _encontro("gob", "pj", turn=0)
    assert combat.end_player_turn(enc, "pj")["turn"] == 0


def test_ciclo_completo_ataque_do_jogador_reacao_e_nova_vez():
    """Ponta a ponta: o jogador abre o combate atacando, o goblin revida, e a vez volta
    ao jogador — que age de novo e o goblin revida outra vez na rodada seguinte."""
    lutadores = {"pj": _jogador(), "gob": _goblin()}
    # iniciativa: pj 18(+2)=20, gob 5(+2)=7 → pj primeiro; o ataque dele já foi o turno
    enc = combat.start(list(lutadores.values()), "pj", _dado(18, 5), player_already_acted=True)
    rel = combat.run_enemy_turns(enc, lutadores, "pj", _dado(12, 4))   # goblin acerta 6
    assert rel.fighters["pj"].hp == 14 and rel.encounter["turn"] == 0
    # a ordem voltou ao topo: começou a rodada 2, e ela abre na vez do jogador
    assert rel.encounter["round"] == 2

    enc2 = combat.end_player_turn(rel.encounter, "pj")
    rel2 = combat.run_enemy_turns(enc2, rel.fighters, "pj", _dado(12, 4))
    assert rel2.fighters["pj"].hp == 8
    assert rel2.encounter["round"] == 3 and rel2.encounter["turn"] == 0
