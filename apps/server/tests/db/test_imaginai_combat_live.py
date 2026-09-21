"""Combate v2 e morte, gravando no banco real (ver conftest: pula sem TEST_DATABASE_URL)."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from .conftest import migrar, pytestmark  # noqa: F401


def _async(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _mundo(db):
    """Campanha em jogo com: jogador, um goblin hostil e o Gromm (aliado em potencial)."""
    from aiworkspace.imaginai import service, setup
    from aiworkspace.models import Chat, ImaginaiCampaign, User
    from aiworkspace.schemas.imaginai import CampaignCreate

    user = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
    db.add(user)
    await db.flush()
    chat = Chat(user_id=user.id, title="t", mini_app="imaginai")
    db.add(chat)
    await db.commit()
    snap = await service.create_campaign(db, user.id, CampaignCreate(chat_id=chat.id))
    campaign = await db.get(ImaginaiCampaign, uuid.UUID(snap["campaign"]["id"]))
    await setup.set_concept(db, campaign, {"name": "C", "genre": "g", "premise": "p"})
    await setup.build_world(db, campaign, user.id, {
        "locations": [{"name": "Praça", "visibility": "known"}], "starting_location": "Praça",
        "npcs": [
            # vida alta de propósito: com 7 PV, o Gromm (aliado, 1d8+3) às vezes o matava
            # antes da vez do jogador — o combate acabava e a trava de "atordoado só passa
            # a vez", que só vale em combate, sumia do teste (falha em ~1 de 6 execuções)
            {"name": "Goblin", "kind": "creature", "hostile": True, "hp": 60, "ac": 13,
             "location": "Praça", "visibility": "known",
             "actions": [{"name": "Dardo venenoso", "attack_bonus": 4, "damage": "1d4",
                          "condition": "poisoned", "rounds": 2}]},
            {"name": "Gromm", "hp": 15, "ac": 12, "location": "Praça", "visibility": "known",
             "actions": [{"name": "Machado", "attack_bonus": 5, "damage": "1d8+3"}]},
        ],
    })
    campaign.setup_stage = "play"
    await db.commit()
    return user, campaign


async def _cenario(url: str) -> dict:
    from aiworkspace.imaginai import effects, encounters, service
    from aiworkspace.models import ImaginaiEntity
    from aiworkspace.schemas.imaginai import ActionRequest

    eng = create_async_engine(_async(url))
    out: dict = {}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user, campaign = await _mundo(db)
            out["aliado"] = await effects.set_ally(db, campaign, user.id, "Gromm", True)
            player = await effects._player(db, campaign)
            combate = await encounters.start(db, campaign, user.id, player, player_already_acted=False)
            out["lados"] = {linha["name"]: linha["side"] for linha in (combate or {}).get("order", [])}
            await db.commit()

            out["save"] = await effects.saving_throw(db, campaign, user.id, "player", "DES", 10)
            out["veneno"] = await effects.apply_effect(
                db, campaign, user.id, "player", condition="envenenado", rounds=2)

            # atordoado em combate: só pode passar a vez
            await effects.apply_effect(db, campaign, user.id, "player", condition="stunned", rounds=1)
            player = await effects._player(db, campaign)
            req = lambda t: ActionRequest(actor_id=player.id, action_type=t)  # noqa: E731
            out["atordoado_ataca"] = await service.resolve_action(db, user.id, campaign.id, req("examine"))
            out["passa_a_vez"] = await service.resolve_action(db, user.id, campaign.id, req("pass_turn"))
            await effects.apply_effect(db, campaign, user.id, "player", remove_condition="stunned")

            # queda: dano grande em dados derruba o personagem
            out["queda"] = await effects.apply_effect(db, campaign, user.id, "player", damage="10d10+50")
            out["caido_age"] = await service.resolve_action(db, user.id, campaign.id, req("examine"))
            out["fate_morte"] = await effects.decide_fate(db, campaign, user.id, "dead", "Caiu do penhasco.")
            out["morto_age"] = await service.resolve_action(db, user.id, campaign.id, req("examine"))

            out["novo"] = await effects.new_character(db, campaign, user.id)
            rows = list(await db.scalars(select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.kind.in_(("character", "npc")))))
            out["personagens"] = {e.key: (e.kind, e.name) for e in rows}
            out["stage"] = campaign.setup_stage
    finally:
        await eng.dispose()
    return out


def test_aliado_morte_e_personagem_novo(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_cenario(banco))

    assert r["aliado"]["ally"] is True and r["aliado"]["can_fight"] is True
    assert r["lados"]["Gromm"] == "ally" and r["lados"]["Goblin"] == "hostile"

    assert r["save"]["ability"] == "dexterity" and r["save"]["dc"] == 10
    assert r["veneno"]["condition"] == {"key": "poisoned", "rounds": 2}

    assert r["atordoado_ataca"]["reason_code"] == "incapacitated"
    assert r["passa_a_vez"]["status"] == "resolved"

    assert r["queda"]["dropped"] is True and "fate" in r["queda"]["player_down"]
    assert r["caido_age"]["reason_code"] == "character_down"
    assert r["fate_morte"]["outcome"] == "dead"
    assert r["morto_age"]["reason_code"] == "character_dead"

    assert r["stage"] == "character"
    assert r["personagens"]["player"] == ("character", "Nome do personagem")
    assert any(k.startswith("falecido-") and v[0] == "npc" for k, v in r["personagens"].items())


async def _reviver(url: str) -> dict:
    from aiworkspace.imaginai import effects

    eng = create_async_engine(_async(url))
    out: dict = {}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            user, campaign = await _mundo(db)
            out["reviver_vivo"] = await _tentar(effects.decide_fate(db, campaign, user.id, "revived", "x"))
            await effects.apply_effect(db, campaign, user.id, "player", damage="100")
            await effects.decide_fate(db, campaign, user.id, "dead", "x")
            out["reviver"] = await effects.decide_fate(db, campaign, user.id, "revived", "Ritual.")
            out["status"] = effects.player_status(await effects._player(db, campaign))
            out["dado"] = await effects.roll_dice(db, campaign, user.id, "2d6+1", "quantos guardas")
    finally:
        await eng.dispose()
    return out


async def _tentar(coro):
    from aiworkspace.imaginai import effects

    try:
        return await coro
    except effects.EffectError as exc:
        return {"erro": str(exc)}


def test_reviver_so_quem_morreu_e_mesma_ficha_volta(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_reviver(banco))
    assert "erro" in r["reviver_vivo"]
    assert r["reviver"]["outcome"] == "revived" and r["status"] == "alive"
    assert 3 <= r["dado"]["total"] <= 13


async def _fuga(url: str, desengajar: bool) -> dict:
    from sqlalchemy import select as _select

    from aiworkspace.imaginai import effects, encounters, service, setup
    from aiworkspace.models import Chat, ImaginaiCampaign, ImaginaiEntity, User
    from aiworkspace.schemas.imaginai import ActionRequest, CampaignCreate

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
            await setup.build_world(db, campaign, user.id, {
                "locations": [{"name": "Praça", "visibility": "known", "connections": ["Portão"]},
                              {"name": "Portão", "visibility": "known"}],
                "starting_location": "Praça",
                "npcs": [{"name": "Goblin", "kind": "creature", "hostile": True, "hp": 7,
                          "location": "Praça", "visibility": "known",
                          "actions": [{"name": "Adaga", "attack_bonus": 3, "damage": "1d4"}]}],
            })
            campaign.setup_stage = "play"
            player = await effects._player(db, campaign)
            state = dict(player.state)
            state["dnd5e"] = {**state["dnd5e"], "hp": {"current": 30, "max": 30}}
            player.state = state
            await db.commit()
            await encounters.start(db, campaign, user.id, player, player_already_acted=False)
            await db.commit()
            portao = await db.scalar(_select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id, ImaginaiEntity.name == "Portão"))
            player = await effects._player(db, campaign)
            out["fuga"] = await service.resolve_action(db, user.id, campaign.id, ActionRequest(
                actor_id=player.id, action_type="move", target_id=portao.id,
                parameters={"disengage": True} if desengajar else {}))
            player = await effects._player(db, campaign)
            out["chegou"] = player.location_id == portao.id

            mapa = await service.map_snapshot(db, campaign)
            ids = [loc["id"] for loc in mapa["locations"]]
            gravado = await service.save_map_positions(db, campaign, {ids[0]: {"x": 12.5, "y": 80}})
            out["pos"] = next(loc for loc in gravado["locations"] if loc["id"] == ids[0])
            limpo = await service.save_map_positions(db, campaign, {ids[0]: None})
            out["pos_limpa"] = next(loc for loc in limpo["locations"] if loc["id"] == ids[0])
    finally:
        await eng.dispose()
    return out


def test_fugir_pelo_caminho_do_mapa_provoca_ataque_de_oportunidade(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_fuga(banco, desengajar=False))
    assert r["fuga"]["status"] == "resolved"                 # caminho do mapa = rota conhecida
    assert len(r["fuga"]["opportunity_attacks"]) == 1
    assert r["chegou"] is True
    assert r["pos"]["x"] == 12.5 and r["pos"]["y"] == 80
    assert r["pos_limpa"]["x"] is None


def test_desengajar_evita_o_ataque_de_oportunidade(banco, engine):
    migrar(engine, "head")
    r = asyncio.run(_fuga(banco, desengajar=True))
    assert "opportunity_attacks" not in r["fuga"] and r["chegou"] is True
