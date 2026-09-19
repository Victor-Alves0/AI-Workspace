"""Sessão zero do Imaginai no banco real: da campanha vazia à aventura com ficha pronta.

As regras puras têm teste próprio (test_imaginai_character_build.py); aqui o que se
prova é a GRAVAÇÃO — o que a ferramenta devolve é o que ficou no banco, a rolagem não
se repete, e o equipamento entra no inventário uma vez só.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from .conftest import migrar, pytestmark  # noqa: F401


def _async(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _fluxo(url: str) -> dict:
    from aiworkspace.imaginai import service, setup
    from aiworkspace.models import Chat, ImaginaiCampaign, User
    from aiworkspace.schemas.imaginai import CampaignCreate

    eng = create_async_engine(_async(url))
    out: dict = {}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
            db.add(user)
            await db.flush()
            chat = Chat(user_id=user.id, title="t", mini_app="imaginai")
            db.add(chat)
            await db.commit()
            snap = await service.create_campaign(db, user.id, CampaignCreate(chat_id=chat.id))
            campaign = await db.get(ImaginaiCampaign, uuid.UUID(snap["campaign"]["id"]))
            campaign.setup_stage = "character"          # mundo já criado (etapa anterior)
            await db.commit()

            dados = iter([6, 5, 4, 1] * 6)
            rolagem = await setup.roll_abilities(db, campaign, roller=lambda s: next(dados))
            out["rolagem"] = rolagem
            out["rolagem_de_novo"] = await setup.roll_abilities(db, campaign, roller=lambda s: 6)

            # conceito primeiro, atributos depois — as chamadas se acumulam
            await setup.set_character(db, campaign, {
                "name": "Victor Alves", "class": "Feiticeiro", "race": "Humano",
                "background": "Coletor de água de chuva",
                "background_skills": ["Sobrevivência", "Percepção"],
            })
            out["antes_de_comecar"] = await _tentar(setup.begin_adventure(db, campaign))
            final = await setup.set_character(db, campaign, {
                "abilities": {"FOR": 15, "DES": 15, "CON": 15, "INT": 15, "SAB": 15, "CAR": 15},
                "class_skills": ["Persuasão", "Intuição"],
                "backstory": "Achou um dente de deus no rio seco.",
            })
            out["final"] = final
            out["inicio"] = await setup.begin_adventure(db, campaign)
            out["inventario"] = await service.inventory_snapshot(db, campaign)
            snap = await service.public_snapshot(db, campaign)
            out["ficha"] = snap["character"]
    finally:
        await eng.dispose()
    return out


async def _tentar(coro):
    from aiworkspace.imaginai import setup

    try:
        return await coro
    except setup.SetupError as exc:
        return {"erro": str(exc)}


def test_sessao_zero_grava_a_ficha_calculada_e_entrega_o_equipamento(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_fluxo(banco))

    assert r["rolagem"]["values"] == [15] * 6               # 6,5,4 (descarta o 1)
    assert r["rolagem_de_novo"]["already_rolled"] is True   # rolar de novo não troca nada
    assert r["rolagem_de_novo"]["values"] == [15] * 6

    assert "abilities" in r["antes_de_comecar"]["erro"]     # sem atributos, não começa
    assert r["final"]["missing"] == []

    dnd = r["ficha"]["state"]["dnd5e"]
    assert r["ficha"]["name"] == "Victor Alves"
    assert dnd["class"] == "Feiticeiro"
    assert dnd["attributes"]["charisma"] == 16              # 15 + humano
    assert dnd["hp"]["max"] == 6 + 3                        # d6 + CON(+3)
    assert dnd["saving_throws"]["charisma"]["proficient"]
    assert {"survival", "perception", "persuasion", "insight"} <= set(dnd["skills"])

    nomes = {i["name"] for i in r["inventario"]["items"]}
    assert {"Besta leve", "Adaga", "Bolsa de componentes"} <= nomes
    assert r["inventario"]["currencies"]["gp"] >= 10


@pytest.mark.parametrize("campo", ["class", "race"])
def test_campos_textuais_nao_viram_numero_inventado(campo):
    """Guarda de regressão do caso real: sem classe/raça registradas, a ficha não fica
    'pronta' só porque o narrador escreveu isso no chat."""
    from aiworkspace.imaginai import dnd5e_build

    _, missing, _ = dnd5e_build.derive({})
    assert any(m.startswith(campo) for m in missing)


async def _expansao(url: str) -> dict:
    from sqlalchemy import select

    from aiworkspace.imaginai import service, setup
    from aiworkspace.models import Chat, ImaginaiCampaign, ImaginaiEntity, User
    from aiworkspace.schemas.imaginai import CampaignCreate

    eng = create_async_engine(_async(url))
    out: dict = {}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
            db.add(user)
            await db.flush()
            chat = Chat(user_id=user.id, title="t", mini_app="imaginai")
            db.add(chat)
            await db.commit()
            snap = await service.create_campaign(db, user.id, CampaignCreate(chat_id=chat.id))
            campaign = await db.get(ImaginaiCampaign, uuid.UUID(snap["campaign"]["id"]))
            await setup.set_concept(db, campaign, {"name": "C", "genre": "g", "premise": "p"})
            # mundo "antigo": sem caminhos, como a Coroa de Cinzas
            await setup.build_world(db, campaign, user.id, {"locations": [
                {"name": "Vilagris", "visibility": "known"}, {"name": "Catedral", "visibility": "known"},
            ], "starting_location": "Vilagris"})
            out["antes"] = await service.map_snapshot(db, campaign)
            out["expansao"] = await setup.expand_world(db, campaign, user.id, {
                "locations": [{"name": "Vilagris", "connections": ["Catedral"]},
                              {"name": "Forja", "visibility": "known", "connections": ["Vilagris"]}],
                "npcs": [{"name": "Ferreiro", "location": "Forja"}],
                "lore": ["Os sinos tocam sozinhos."],
            })
            out["de_novo"] = await setup.expand_world(db, campaign, user.id, {
                "locations": [{"name": "Forja"}], "npcs": [{"name": "Ferreiro"}],
            })
            out["depois"] = await service.map_snapshot(db, campaign)
            forja = await db.scalar(select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id, ImaginaiEntity.name == "Forja"))
            out["ferreiro_na_forja"] = await db.scalar(select(ImaginaiEntity.id).where(
                ImaginaiEntity.name == "Ferreiro", ImaginaiEntity.location_id == forja.id))
            out["grimorio"] = await service.write_spells(db, campaign, [
                {"name": "Raio de Fogo", "level": 0, "damage": "1d10", "range": "36 m",
                 "description": "Um cisco de fogo."},
            ])
    finally:
        await eng.dispose()
    return out


def test_expandir_liga_o_mapa_antigo_e_nao_duplica(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_expansao(banco))
    assert r["antes"]["routes"] == []
    assert r["expansao"]["added"]["locations"] == ["Forja"]
    assert r["expansao"]["added"]["new_paths"] == 2
    assert r["de_novo"]["added"] == {"locations": [], "npcs": [], "factions": [],
                                     "lore_entries": 0, "new_paths": 0}
    assert len(r["depois"]["routes"]) == 2
    assert r["ferreiro_na_forja"] is not None
    raio = r["grimorio"]["spells"][0]
    assert raio["range"] == "36 m" and raio["description"] == "Um cisco de fogo."
