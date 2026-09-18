"""Sessão zero do Imaginai: a IA conduz a criação da campanha com o jogador.

Uma campanha nova passa por três etapas, gravadas em `campaign.setup_stage`:

    concept   → o narrador cumprimenta, pergunta a ideia do jogador e fecha o conceito
                (nome, gênero, tema, tom, premissa). O que o jogador não disser, ele
                pergunta ou propõe. Fechado o conceito, cria o MUNDO de uma vez.
    character → o mundo existe; o jogador cria o personagem (ficha + história), pela
                tela ou contando ao narrador.
    play      → o narrador insere o personagem no local inicial e o jogo começa.

Por que etapas no servidor, e não "o modelo segue um roteiro": com a etapa gravada, cada
turno recebe SÓ a instrução e a ferramenta da etapa atual. O modelo não precisa lembrar
em que ponto está, não cria o mundo duas vezes e não começa a aventura sem personagem —
e uma regeneração no meio do caminho não bagunça nada.

As regras (o que falta, o que é um mundo válido) são puras e testadas sem banco; as
funções `async` só gravam.
"""

from __future__ import annotations

import copy
import re
import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Chat, ImaginaiCampaign, ImaginaiEntity

STAGES = ("concept", "character", "play")

# Conceito: o narrador precisa de nome, gênero e premissa para criar um mundo coerente.
# Tema e tom enriquecem, mas ele pode propor sozinho.
CONCEPT_REQUIRED = ("name", "genre", "premise")
CONCEPT_FIELDS = ("name", "genre", "theme", "tone", "premise")

# placeholders com que a campanha nasce — contam como "ainda não definido"
_PLACEHOLDERS = {"nome da campanha", "nome do personagem", "classe", "local inicial", ""}

# tetos: um mundo inicial rico, mas que cabe no contexto dos turnos seguintes
MAX_LOCATIONS = 12
MAX_NPCS = 20
MAX_FACTIONS = 8
MAX_LORE = 20


class SetupError(ValueError):
    """Pedido inválido para a etapa atual — a mensagem volta ao narrador."""


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _blank(value: Any) -> bool:
    return _text(value, 300).casefold() in _PLACEHOLDERS


# --------------------------------------------------------------------------- #
# Conceito                                                                     #
# --------------------------------------------------------------------------- #
def concept_of(campaign: ImaginaiCampaign) -> dict[str, str]:
    settings = campaign.settings or {}
    return {
        "name": "" if _blank(campaign.name) else campaign.name,
        "genre": str(settings.get("genre") or ""),
        "theme": str(settings.get("theme") or ""),
        "tone": str(settings.get("tone") or ""),
        "premise": str(settings.get("premise") or ""),
    }


def missing_concept(concept: dict[str, str]) -> list[str]:
    return [field for field in CONCEPT_REQUIRED if _blank(concept.get(field))]


# --------------------------------------------------------------------------- #
# Mundo                                                                        #
# --------------------------------------------------------------------------- #
def slug(name: str, taken: set[str]) -> str:
    """Chave estável e única dentro da campanha (nomes podem se repetir)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:60] or "entidade"
    key = base
    while key in taken:
        key = f"{base}-{secrets.token_hex(2)}"
    taken.add(key)
    return key


def _visibility(value: Any, default: str) -> str:
    v = str(value or "").strip().casefold()
    return v if v in {"known", "aware", "hidden"} else default


def _int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


_DAMAGE_RE = re.compile(r"^(\d+d\d+|\d+)([+-]\d+)?$")


def validate_world(spec: Any) -> dict[str, Any]:
    """Normaliza o mundo proposto pelo narrador — ou explica o que falta.

    O modelo escreve o mundo livremente; aqui ele vira algo que o banco aceita: nomes
    obrigatórios, tetos de quantidade, local inicial que existe, NPC apontando para um
    local conhecido, estatísticas de combate dentro de faixas sãs."""
    if not isinstance(spec, dict):
        raise SetupError("Envie o mundo como um objeto com locations, npcs, factions e lore.")

    locations = []
    for raw in (spec.get("locations") or [])[:MAX_LOCATIONS]:
        if isinstance(raw, dict) and _text(raw.get("name"), 255):
            locations.append({
                "name": _text(raw["name"], 255),
                "description": _text(raw.get("description"), 3000),
                "secret": _text(raw.get("secret"), 5000),
                "visibility": _visibility(raw.get("visibility"), "aware"),
            })
    if not locations:
        raise SetupError("O mundo precisa de ao menos um local (inclua o local inicial).")

    nomes = {loc["name"].casefold(): loc["name"] for loc in locations}
    inicial = _text(spec.get("starting_location"), 255)
    if inicial.casefold() not in nomes:
        inicial = locations[0]["name"]          # sem indicação válida: o primeiro local

    npcs = []
    for raw in (spec.get("npcs") or [])[:MAX_NPCS]:
        if not (isinstance(raw, dict) and _text(raw.get("name"), 255)):
            continue
        kind = "creature" if str(raw.get("kind") or "").casefold() == "creature" else "npc"
        local = _text(raw.get("location"), 255)
        local = nomes.get(local.casefold(), "")
        no_inicio = local.casefold() == inicial.casefold()
        dano = _text(raw.get("damage"), 16).replace(" ", "").casefold()
        npcs.append({
            "name": _text(raw["name"], 255),
            "kind": kind,
            "description": _text(raw.get("description"), 3000),
            "persona": _text(raw.get("persona") or raw.get("secret"), 8000),
            "location": local or None,
            "hostile": bool(raw.get("hostile")),
            "hp": _int(raw.get("hp"), 12 if kind == "creature" else 10, 1, 999),
            "ac": _int(raw.get("ac"), 12 if kind == "creature" else 10, 1, 30),
            "attack_bonus": _int(raw.get("attack_bonus"), 3, -5, 20),
            "damage": dano if _DAMAGE_RE.fullmatch(dano) else "",
            # quem está no local inicial é visto de cara; o resto se descobre jogando
            "visibility": _visibility(raw.get("visibility"), "known" if no_inicio else "hidden"),
        })

    factions = []
    for raw in (spec.get("factions") or [])[:MAX_FACTIONS]:
        if isinstance(raw, dict) and _text(raw.get("name"), 255):
            factions.append({
                "name": _text(raw["name"], 255),
                "description": _text(raw.get("description"), 3000),
                "secret": _text(raw.get("secret"), 5000),
                "visibility": _visibility(raw.get("visibility"), "aware"),
            })

    lore = [_text(line, 600) for line in (spec.get("lore") or [])[:MAX_LORE] if _text(line, 600)]
    return {
        "locations": locations, "starting_location": inicial, "npcs": npcs,
        "factions": factions, "lore": lore,
        "opening_scene": _text(spec.get("opening_scene"), 5000),
    }


def _visibility_state(visibility: str) -> dict[str, Any]:
    """Mesma convenção do construtor de mundo (WorldBuilder.tsx)."""
    if visibility == "known":
        return {"discovered": True}
    if visibility == "aware":
        return {"discovery": "aware"}
    return {"hidden": True}


# --------------------------------------------------------------------------- #
# Personagem                                                                   #
# --------------------------------------------------------------------------- #
def missing_character(player: ImaginaiEntity | None) -> list[str]:
    """Para começar a aventura o personagem precisa de nome e classe de verdade."""
    if player is None:
        return ["name", "class"]
    dnd = (player.state or {}).get("dnd5e", player.state or {})
    faltando = []
    if _blank(player.name):
        faltando.append("name")
    if _blank(dnd.get("class") if isinstance(dnd, dict) else ""):
        faltando.append("class")
    return faltando


# --------------------------------------------------------------------------- #
# Gravação                                                                     #
# --------------------------------------------------------------------------- #
async def _player(db: AsyncSession, campaign: ImaginaiCampaign) -> ImaginaiEntity | None:
    return await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        ).with_for_update()
    )


def _require(campaign: ImaginaiCampaign, *stages: str) -> None:
    if campaign.setup_stage not in stages:
        atual = {"concept": "conceito", "character": "criação do personagem", "play": "aventura em curso"}
        raise SetupError(
            f"A campanha está na etapa '{atual.get(campaign.setup_stage, campaign.setup_stage)}'; "
            "esta ação não se aplica agora."
        )


async def set_concept(db: AsyncSession, campaign: ImaginaiCampaign, fields: dict[str, Any]) -> dict[str, Any]:
    """Grava os campos do conceito que vierem (atualização parcial)."""
    _require(campaign, "concept")
    fields = fields if isinstance(fields, dict) else {}
    settings = copy.deepcopy(campaign.settings or {})
    nome = _text(fields.get("name"), 255)
    if nome:
        campaign.name = nome
        chat = await db.get(Chat, campaign.chat_id)
        if chat is not None:
            chat.title = nome                       # o chat passa a se chamar a campanha
    for field in ("genre", "theme", "tone"):
        valor = _text(fields.get(field), 200)
        if valor:
            settings[field] = valor
    premissa = _text(fields.get("premise"), 5000)
    if premissa:
        settings["premise"] = premissa
    campaign.settings = settings
    await db.commit()
    concept = concept_of(campaign)
    return {"stage": campaign.setup_stage, "concept": concept, "missing": missing_concept(concept)}


async def build_world(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, spec: Any,
) -> dict[str, Any]:
    """Cria o mundo inicial inteiro de uma vez e passa para a criação do personagem."""
    _require(campaign, "concept")
    faltando = missing_concept(concept_of(campaign))
    if faltando:
        raise SetupError(
            f"Feche o conceito antes de criar o mundo (falta: {', '.join(faltando)}). "
            "Use set_concept."
        )
    world = validate_world(spec)

    taken = set(await db.scalars(
        select(ImaginaiEntity.key).where(ImaginaiEntity.campaign_id == campaign.id)
    ))
    # o local inicial JÁ existe (a campanha nasce com ele, com o personagem dentro):
    # ele vira o local escolhido — ninguém precisa ser movido
    inicial = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "location",
            ImaginaiEntity.key == "starting-location",
        ).with_for_update()
    )
    locais: dict[str, ImaginaiEntity] = {}
    for loc in world["locations"]:
        if inicial is not None and loc["name"].casefold() == world["starting_location"].casefold():
            inicial.name = loc["name"]
            inicial.description = loc["description"]
            inicial.private_notes = loc["secret"] or None
            inicial.state = {**(inicial.state or {}), "discovered": True}
            locais[loc["name"].casefold()] = inicial
            continue
        entity = ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind="location",
            key=slug(loc["name"], taken), name=loc["name"], description=loc["description"],
            private_notes=loc["secret"] or None,
            state={**_visibility_state(loc["visibility"]), "map": {}},
        )
        db.add(entity)
        locais[loc["name"].casefold()] = entity
    await db.flush()

    for npc in world["npcs"]:
        dnd: dict[str, Any] = {
            "hp": {"current": npc["hp"], "max": npc["hp"]},
            "armor_class": npc["ac"], "conditions": [],
        }
        if npc["damage"]:
            dnd["attacks"] = [{
                "key": "attack", "name": "Ataque",
                "attack_modifier": npc["attack_bonus"], "damage": npc["damage"],
            }]
        state = {**_visibility_state(npc["visibility"]), "dnd5e": dnd}
        if npc["hostile"]:
            state["hostile"] = True
        local = locais.get((npc["location"] or "").casefold())
        db.add(ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind=npc["kind"],
            key=slug(npc["name"], taken), name=npc["name"], description=npc["description"],
            location_id=local.id if local is not None else None,
            private_notes=npc["persona"] or None, state=state,
        ))

    for faction in world["factions"]:
        db.add(ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind="faction",
            key=slug(faction["name"], taken), name=faction["name"],
            description=faction["description"], private_notes=faction["secret"] or None,
            state=_visibility_state(faction["visibility"]),
        ))

    settings = copy.deepcopy(campaign.settings or {})
    if world["lore"]:
        settings["lore"] = world["lore"]
    if world["opening_scene"]:
        settings["opening_scene"] = world["opening_scene"]
    campaign.settings = settings
    campaign.setup_stage = "character"
    await db.commit()
    return {
        "stage": campaign.setup_stage,
        "starting_location": world["starting_location"],
        "created": {
            "locations": [loc["name"] for loc in world["locations"]],
            "npcs": [npc["name"] for npc in world["npcs"]],
            "factions": [f["name"] for f in world["factions"]],
            "lore_entries": len(world["lore"]),
        },
    }


async def set_character(db: AsyncSession, campaign: ImaginaiCampaign, fields: dict[str, Any]) -> dict[str, Any]:
    """Registra o que o jogador contou do personagem (ou o que ele pediu para criar)."""
    from ..schemas.imaginai import CharacterUpdate
    from .service import _merge_character_setup

    _require(campaign, "character")
    fields = fields if isinstance(fields, dict) else {}
    player = await _player(db, campaign)
    if player is None:
        raise SetupError("Personagem da campanha não encontrado.")
    body = CharacterUpdate(
        name=_text(fields.get("name"), 255) or None,
        character_class=_text(fields.get("class") or fields.get("character_class"), 80) or None,
        level=_int(fields.get("level"), 1, 1, 20) if fields.get("level") else None,
        ancestry=_text(fields.get("ancestry"), 120) or None,
        background=_text(fields.get("background"), 120) or None,
        alignment=_text(fields.get("alignment"), 80) or None,
        backstory=str(fields.get("backstory") or "").strip()[:8000] or None,
    )
    if body.name:
        player.name = body.name
    player.state = _merge_character_setup(player.state or {}, body)
    await db.commit()
    return {"stage": campaign.setup_stage, "character": character_summary(player),
            "missing": missing_character(player)}


def character_summary(player: ImaginaiEntity | None) -> dict[str, Any]:
    if player is None:
        return {}
    dnd = (player.state or {}).get("dnd5e", player.state or {})
    dnd = dnd if isinstance(dnd, dict) else {}
    return {
        "name": "" if _blank(player.name) else player.name,
        "class": "" if _blank(dnd.get("class")) else dnd.get("class"),
        "level": dnd.get("level", 1),
        "ancestry": dnd.get("ancestry", ""),
        "background": dnd.get("background", ""),
        "alignment": dnd.get("alignment", ""),
        "backstory": dnd.get("backstory", ""),
    }


async def begin_adventure(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    """Fecha a sessão zero. Só com personagem de verdade — senão a abertura não tem quem
    inserir no mundo."""
    _require(campaign, "character")
    player = await _player(db, campaign)
    faltando = missing_character(player)
    if faltando:
        raise SetupError(
            f"O personagem ainda não está pronto (falta: {', '.join(faltando)}). Peça ao "
            "jogador ou registre com set_character."
        )
    campaign.setup_stage = "play"
    await db.commit()
    return {"stage": "play", "character": character_summary(player)}


async def status(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
        )
    )
    concept = concept_of(campaign)
    return {
        "stage": campaign.setup_stage,
        "concept": concept,
        "concept_missing": missing_concept(concept),
        "character": character_summary(player),
        "character_missing": missing_character(player),
    }
