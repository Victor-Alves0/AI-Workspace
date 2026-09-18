"""Imaginai: o que o JOGADOR pode ver.

O mundo é autoritativo, e metade disso é sigilo: a persona de um NPC, a lore secreta e
as entidades ainda não descobertas existem no banco mas NÃO podem sair nos contratos
que alimentam a interface — nem no contexto público do turno. Um vazamento aqui não
quebra nada visivelmente: só entrega ao jogador o que a campanha guardava, e ninguém
percebe até a surpresa já ter sido estragada.

Herméticos: as funções de visibilidade são puras sobre objetos do modelo, então os
testes montam entidades em memória, sem banco.
"""
from __future__ import annotations

import uuid

from aiworkspace.imaginai import service, turns
from aiworkspace.models import ImaginaiEntity

SECRET = "O ferreiro é o assassino e guarda a chave no porão."


def _entity(**kwargs) -> ImaginaiEntity:
    base = {
        "id": uuid.uuid4(),
        "campaign_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "kind": "npc",
        "key": "npc",
        "name": "NPC",
        "description": "",
        "location_id": None,
        "owner_entity_id": None,
        "state": {},
        "private_notes": None,
        "active": True,
    }
    base.update(kwargs)
    return ImaginaiEntity(**base)


def _player(location_id=None, discovered=()) -> ImaginaiEntity:
    return _entity(
        kind="character", key="player", name="Herói", location_id=location_id,
        state={"dnd5e": {"discovered_entity_ids": [str(i) for i in discovered]}},
    )


# --------------------------------------------------------------------------- #
# Lore secreta                                                                 #
# --------------------------------------------------------------------------- #
def test_contrato_publico_nunca_carrega_a_lore_secreta():
    """`private_notes` é o campo de segredos do mestre: não pode sair no snapshot."""
    npc = _entity(name="Ferreiro", private_notes=SECRET, description="Um ferreiro rude.")
    out = service._entity_out(npc)
    assert "private_notes" not in out
    assert SECRET not in str(out)
    assert out["description"] == "Um ferreiro rude."   # o público continua vindo


def test_entidade_do_turno_nunca_carrega_a_lore_secreta():
    """Mesmo com `include_state`, o que vai ao modelo como cena é só o público."""
    npc = _entity(name="Ferreiro", private_notes=SECRET, state={"hp": 10, "hidden": False})
    for include_state in (False, True):
        out = turns._public_entity(npc, include_state=include_state)
        assert SECRET not in str(out), include_state
        assert "private_notes" not in out


# --------------------------------------------------------------------------- #
# Descoberta: existir não é a mesma coisa que ser conhecido                     #
# --------------------------------------------------------------------------- #
def test_entidade_oculta_no_mesmo_local_permanece_desconhecida():
    """Um assassino escondido na taverna está no mesmo local — e ainda assim invisível
    até ser descoberto. Sem isto, estar na sala entregaria a emboscada."""
    place = uuid.uuid4()
    player = _player(location_id=place)
    lurker = _entity(name="Assassino", location_id=place, state={"hidden": True})

    assert turns._entity_known(player, lurker) is False
    assert turns._entity_accessible(player, lurker) is False


def test_descoberta_libera_a_entidade_oculta():
    place = uuid.uuid4()
    lurker = _entity(name="Assassino", location_id=place, state={"hidden": True})
    player = _player(location_id=place, discovered=[lurker.id])

    assert turns._entity_known(player, lurker) is True
    assert turns._entity_accessible(player, lurker) is True


def test_entidade_conhecida_em_outro_local_nao_fica_ao_alcance():
    """Conhecer o rei não põe o rei ao alcance da espada: `known` ≠ `accessible`."""
    king = _entity(name="Rei", location_id=uuid.uuid4(), state={"discovery": "known"})
    player = _player(location_id=uuid.uuid4())

    assert turns._entity_known(player, king) is True
    assert turns._entity_accessible(player, king) is False


def test_o_que_o_personagem_carrega_acompanha_ele():
    """Item do jogador é sempre dele — mesmo sem local (está na mochila)."""
    player = _player(location_id=uuid.uuid4())
    sword = _entity(kind="item", name="Espada", owner_entity_id=player.id, location_id=None)

    assert turns._entity_known(player, sword) is True
    assert turns._entity_accessible(player, sword) is True


def test_visibilidade_do_codex_ignora_entidade_nao_descoberta():
    """A busca do Codex só enxerga o que o personagem já conheceu — ausência de
    resultado nunca revela que a entidade existe."""
    player = _player()
    unknown = _entity(name="Cripta Selada")
    known = _entity(name="Praça", state={"discovery": "known"})

    assert service._visible_to_player(player, unknown) is False
    assert service._visible_to_player(player, known) is True
