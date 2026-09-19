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
from . import dnd5e_build
from .combat import Roller, secure_roller

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


def _actions(raw: Any) -> list[dict[str, Any]]:
    """Ações de criatura: ataque ({name, attack_bonus, damage}) ou habilidade com teste
    de resistência ({name, save, dc, damage, half, condition, rounds}) — normalizadas
    pelo mesmo código que o combate usa."""
    from . import combat

    out = []
    for item in (raw if isinstance(raw, list) else [])[:6]:
        if not isinstance(item, dict):
            continue
        dano = _text(item.get("damage"), 16).replace(" ", "").casefold()
        item = {**item, "damage": dano if _DAMAGE_RE.fullmatch(dano) else "",
                "attack_modifier": item.get("attack_bonus", item.get("attack_modifier"))}
        acao = combat._normalize_action(item)
        if acao:
            acao["name"] = _text(acao["name"], 80)
            out.append(acao)
    return out


def _damage_list(raw: Any) -> list[str]:
    """Tipos de dano (pt ou en) normalizados para a chave do combate."""
    from . import combat

    return sorted({k for k in (combat.damage_key(v) for v in (raw if isinstance(raw, list) else [])[:13]) if k})


def _saves(raw: Any) -> dict[str, int]:
    from . import combat

    out = {}
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        ability = combat.ability_key(key)
        if ability:
            out[ability] = _int(value, 0, -5, 20)
    return out


def _npc_state(npc: dict[str, Any]) -> dict[str, Any]:
    """Estado de um NPC/criatura novo (build_world e expand_world)."""
    dnd: dict[str, Any] = {"hp": {"current": npc["hp"], "max": npc["hp"]},
                           "armor_class": npc["ac"], "conditions": []}
    acoes = list(npc.get("actions") or [])
    if not acoes and npc["damage"]:
        acoes = [{"key": "attack", "name": "Ataque", "type": "attack",
                  "attack_modifier": npc["attack_bonus"], "damage": npc["damage"]}]
    if acoes:
        dnd["attacks"] = acoes
    if npc.get("saves"):
        dnd["saves"] = npc["saves"]
    for campo in ("resistances", "immunities", "vulnerabilities"):
        if npc.get(campo):
            dnd[campo] = npc[campo]
    state = {**_visibility_state(npc["visibility"]), "dnd5e": dnd}
    if npc["hostile"]:
        state["hostile"] = True
    elif npc.get("ally"):
        state["ally"] = True
    return state


def validate_world(spec: Any, existing: dict[str, str] | None = None) -> dict[str, Any]:
    """Normaliza o mundo proposto pelo narrador — ou explica o que falta.

    O modelo escreve o mundo livremente; aqui ele vira algo que o banco aceita: nomes
    obrigatórios, tetos de quantidade, local inicial que existe, NPC apontando para um
    local conhecido, estatísticas de combate dentro de faixas sãs.

    `existing` ({nome_casefold: nome}) = expansão de um mundo que já existe: pode vir sem
    local novo, e NPCs/caminhos podem apontar para locais antigos."""
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
                "connections": [
                    _text(c, 255) for c in (raw.get("connections") or [])[:MAX_LOCATIONS]
                    if isinstance(c, str) and _text(c, 255)
                ],
            })
    if not locations and existing is None:
        raise SetupError("O mundo precisa de ao menos um local (inclua o local inicial).")

    nomes = dict(existing or {})
    nomes.update({loc["name"].casefold(): loc["name"] for loc in locations})
    # rotas: só entre locais que existem, sem laço, e nos DOIS sentidos (estrada é de mão dupla)
    ligacoes: dict[str, set[str]] = {nome: set() for nome in nomes.values()}
    for loc in locations:
        for alvo in loc["connections"]:
            real = nomes.get(alvo.casefold())
            if real and real != loc["name"]:
                ligacoes[loc["name"]].add(real)
                ligacoes[real].add(loc["name"])
    for loc in locations:
        loc["connections"] = sorted(ligacoes[loc["name"]])
    # caminhos novos que chegam a locais ANTIGOS (a expansão atualiza os dois lados)
    edges = sorted({tuple(sorted((a, b))) for a, alvos in ligacoes.items() for b in alvos})
    inicial = _text(spec.get("starting_location"), 255)
    if inicial.casefold() not in nomes:
        inicial = locations[0]["name"] if locations else ""   # sem indicação: o primeiro local

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
            "actions": _actions(raw.get("actions")),
            "saves": _saves(raw.get("saves")),
            **{campo: _damage_list(raw.get(campo))
               for campo in ("resistances", "immunities", "vulnerabilities")},
            "ally": bool(raw.get("ally")) and not bool(raw.get("hostile")),
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
        "factions": factions, "lore": lore, "edges": edges,
        "opening_scene": _text(spec.get("opening_scene"), 5000),
    }


def _visibility_state(visibility: str) -> dict[str, Any]:
    """Mesma convenção do construtor de mundo (WorldBuilder.tsx)."""
    if visibility == "known":
        return {"discovered": True}
    if visibility == "aware":
        return {"discovery": "aware"}
    return {"hidden": True}


MAX_LORE_TOTAL = 60


async def expand_world(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID, spec: Any,
) -> dict[str, Any]:
    """Faz o mundo crescer sem refazê-lo: locais, NPCs, facções e lore NOVOS, e caminhos
    ligando o novo ao antigo (ou antigos entre si — é assim que um mapa sem rotas ganha
    linhas). Nome que já existe não duplica: um local antigo repetido só recebe caminhos."""
    _require(campaign, "character", "play")
    antigos = {
        e.name.casefold(): e for e in await db.scalars(
            select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.kind == "location",
                ImaginaiEntity.active.is_(True),
            ).with_for_update()
        )
    }
    world = validate_world(spec, existing={k: e.name for k, e in antigos.items()})
    taken = set(await db.scalars(
        select(ImaginaiEntity.key).where(ImaginaiEntity.campaign_id == campaign.id)
    ))
    nomes_pessoas = {
        n.casefold() for n in await db.scalars(
            select(ImaginaiEntity.name).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.kind.in_(("npc", "creature", "faction")),
            )
        )
    }

    locais = dict(antigos)
    novos_locais = []
    for loc in world["locations"]:
        if loc["name"].casefold() in locais:
            continue
        entity = ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind="location",
            key=slug(loc["name"], taken), name=loc["name"], description=loc["description"],
            private_notes=loc["secret"] or None,
            state={**_visibility_state(loc["visibility"]), "map": {"connections": []}},
        )
        db.add(entity)
        locais[loc["name"].casefold()] = entity
        novos_locais.append(loc["name"])
    await db.flush()

    novos_caminhos = 0
    for a, b in world["edges"]:
        for origem, destino in ((a, b), (b, a)):
            entity = locais.get(origem.casefold())
            if entity is None:
                continue
            state = copy.deepcopy(entity.state or {})
            mapa = state.get("map") if isinstance(state.get("map"), dict) else {}
            atuais = [str(c) for c in mapa.get("connections") or []]
            if destino.casefold() not in {c.casefold() for c in atuais}:
                atuais.append(destino)
                novos_caminhos += 1
            state["map"] = {**mapa, "connections": atuais}
            entity.state = state

    novos_npcs = []
    for npc in world["npcs"]:
        if npc["name"].casefold() in nomes_pessoas:
            continue
        state = _npc_state(npc)
        local = locais.get((npc["location"] or "").casefold())
        db.add(ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind=npc["kind"],
            key=slug(npc["name"], taken), name=npc["name"], description=npc["description"],
            location_id=local.id if local is not None else None,
            private_notes=npc["persona"] or None, state=state,
        ))
        novos_npcs.append(npc["name"])

    novas_faccoes = []
    for faction in world["factions"]:
        if faction["name"].casefold() in nomes_pessoas:
            continue
        db.add(ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind="faction",
            key=slug(faction["name"], taken), name=faction["name"],
            description=faction["description"], private_notes=faction["secret"] or None,
            state=_visibility_state(faction["visibility"]),
        ))
        novas_faccoes.append(faction["name"])

    settings = copy.deepcopy(campaign.settings or {})
    lore = [str(x) for x in settings.get("lore") or []]
    novas_verdades = [x for x in world["lore"] if x not in lore]
    settings["lore"] = (lore + novas_verdades)[-MAX_LORE_TOTAL:]
    campaign.settings = settings
    await db.commit()
    contagem = {
        "locais": len(novos_locais), "NPCs e criaturas": len(novos_npcs),
        "facções": len(novas_faccoes), "verdades de lore": len(novas_verdades),
        "caminhos no mapa": novos_caminhos // 2,
    }
    resumo = ", ".join(f"+{n} {nome}" for nome, n in contagem.items() if n) or "nada novo"
    return {
        "stage": campaign.setup_stage,
        "added": {"locations": novos_locais, "npcs": novos_npcs, "factions": novas_faccoes,
                  "lore_entries": len(novas_verdades), "new_paths": novos_caminhos // 2},
        # o que o JOGADOR recebe: números, não o conteúdo — o resto se descobre jogando
        "tell_player": f"O mundo cresceu: {resumo}.",
        "spoiler_rule": "Não liste nomes, descrições, segredos nem relações do que foi criado. "
                        "Responda só com tell_player (uma frase) e siga a cena.",
    }


# --------------------------------------------------------------------------- #
# Personagem                                                                   #
# --------------------------------------------------------------------------- #
def _dnd(player: ImaginaiEntity | None) -> dict[str, Any]:
    if player is None:
        return {}
    dnd = (player.state or {}).get("dnd5e", player.state or {})
    return dnd if isinstance(dnd, dict) else {}


def missing_character(player: ImaginaiEntity | None) -> list[str]:
    """O que falta para a ficha estar pronta: nome + tudo o que a criação D&D 5e exige
    (classe, raça, antecedente, atributos distribuídos, perícias). Ficha preenchida à mão
    no painel (`sheet_source: manual`) só precisa de nome e classe."""
    if player is None:
        return ["name", "class"]
    dnd = _dnd(player)
    faltando = []
    if _blank(player.name):
        faltando.append("name")
    if dnd.get("sheet_source") == "manual":
        if _blank(dnd.get("class")):
            faltando.append("class")
        return faltando
    build = dnd.get("build") if isinstance(dnd.get("build"), dict) else {}
    try:
        _, falta_build, _ = dnd5e_build.derive(build, roller=lambda sides: 1)
    except dnd5e_build.BuildError as exc:
        falta_build = [str(exc)]
    return faltando + falta_build


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
            inicial.state = {**(inicial.state or {}), "discovered": True,
                             "map": {"connections": loc["connections"]}}
            locais[loc["name"].casefold()] = inicial
            continue
        entity = ImaginaiEntity(
            campaign_id=campaign.id, user_id=user_id, kind="location",
            key=slug(loc["name"], taken), name=loc["name"], description=loc["description"],
            private_notes=loc["secret"] or None,
            state={**_visibility_state(loc["visibility"]), "map": {"connections": loc["connections"]}},
        )
        db.add(entity)
        locais[loc["name"].casefold()] = entity
    await db.flush()

    for npc in world["npcs"]:
        state = _npc_state(npc)
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


# campos da criação que se acumulam em dnd5e.build (o jogador decide aos poucos)
_BUILD_TEXT = {"class": 80, "race": 120, "background": 120}
_BUILD_LISTS = ("class_skills", "background_skills", "expertise", "spells")


def _merge_build(build: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    build = copy.deepcopy(build)
    aliases = {"class": ("class", "character_class"), "race": ("race", "ancestry"),
               "background": ("background",)}
    for key, names in aliases.items():
        for name in names:
            valor = _text(fields.get(name), _BUILD_TEXT[key])
            if valor:
                build[key] = valor
                break
    if fields.get("level"):
        build["level"] = _int(fields.get("level"), 1, 1, 20)
    metodo = str(fields.get("ability_method") or "").strip()
    if metodo:
        if metodo not in dnd5e_build.METHODS:
            raise SetupError(f"ability_method inválido. Use: {', '.join(dnd5e_build.METHODS)}.")
        if build.get("pool") and metodo != "roll":
            raise SetupError("Os atributos já foram rolados — a rolagem vale; distribua os valores.")
        build["method"] = metodo
    for key in _BUILD_LISTS:
        if isinstance(fields.get(key), list):
            build[key] = fields[key][:40]
    if isinstance(fields.get("skills"), list) and "class_skills" not in fields:
        build["class_skills"] = fields["skills"][:10]
    if isinstance(fields.get("race_bonuses"), dict):
        build["race_bonuses"] = fields["race_bonuses"]
    if str(fields.get("hp_method") or "") in ("average", "roll"):
        build["hp_method"] = fields["hp_method"]
    if isinstance(fields.get("abilities"), dict):
        metodo = build.get("method")
        if not metodo:
            raise SetupError(
                "Antes de distribuir, defina ability_method (roll, standard_array, point_buy ou manual)."
            )
        try:
            atribuicao = dnd5e_build.parse_scores(fields["abilities"])
            pool = [p["total"] for p in build.get("pool") or []]
            build["base_abilities"] = dnd5e_build.validate_abilities(metodo, atribuicao, pool)
        except dnd5e_build.BuildError as exc:
            raise SetupError(str(exc)) from exc
    return build


def _apply_sheet(dnd: dict[str, Any], sheet: dict[str, Any]) -> dict[str, Any]:
    """Grava a ficha calculada sem apagar o que o jogo já mudou (moedas, descobertas)."""
    dnd = copy.deepcopy(dnd)
    dnd.update(sheet)
    dnd.pop("sheet_source", None)
    return dnd


async def roll_abilities(
    db: AsyncSession, campaign: ImaginaiCampaign, roller: Roller = secure_roller,
) -> dict[str, Any]:
    """4d6 (descarta o menor) seis vezes, no servidor. Rola UMA vez por personagem:
    repetir devolve a mesma rolagem — senão bastaria pedir de novo até sair 18."""
    _require(campaign, "concept", "character")
    player = await _player(db, campaign)
    if player is None:
        raise SetupError("Personagem da campanha não encontrado.")
    dnd = _dnd(player)
    build = copy.deepcopy(dnd.get("build") or {})
    novo = not build.get("pool")
    if novo:
        build["pool"] = dnd5e_build.roll_pool(roller)
        build["method"] = "roll"
        player.state = {**(player.state or {}), "dnd5e": {**dnd, "build": build}}
        await db.commit()
    return {
        "stage": campaign.setup_stage, "method": "roll", "already_rolled": not novo,
        "rolls": [{"total": p["total"], "dice": p["dice"]} for p in build["pool"]],
        "values": sorted((p["total"] for p in build["pool"]), reverse=True),
    }


async def set_character(
    db: AsyncSession, campaign: ImaginaiCampaign, fields: dict[str, Any],
    roller: Roller = secure_roller,
) -> dict[str, Any]:
    """Acumula as escolhas do jogador e recalcula a ficha pelas regras (dnd5e_build).
    Nada aqui é "anotado": o que volta é o que ficou gravado. Vale já no conceito: o
    que o jogador contar do personagem antes do mundo existir não se perde."""
    _require(campaign, "concept", "character")
    fields = fields if isinstance(fields, dict) else {}
    player = await _player(db, campaign)
    if player is None:
        raise SetupError("Personagem da campanha não encontrado.")
    dnd = _dnd(player)
    build = _merge_build(dnd.get("build") if isinstance(dnd.get("build"), dict) else {}, fields)
    try:
        sheet, _, build = dnd5e_build.derive(build, roller)
    except dnd5e_build.BuildError as exc:
        raise SetupError(str(exc)) from exc

    nome = _text(fields.get("name"), 255)
    if nome:
        player.name = nome
    for key, limit in (("alignment", 80), ("backstory", 8000)):
        if fields.get(key):
            sheet[key] = str(fields[key]).strip()[:limit]
    dnd = _apply_sheet(dnd, sheet)
    dnd["build"] = build
    player.state = {**(player.state or {}), "dnd5e": dnd}
    await db.commit()
    return {
        "stage": campaign.setup_stage,
        "saved": True,
        "character": character_summary(player),
        "sheet": sheet_view(dnd),
        "missing": missing_character(player),
    }


def sheet_view(dnd: dict[str, Any]) -> dict[str, Any]:
    """O que o narrador mostra ao jogador depois de cada passo (números do servidor)."""
    keys = ("attributes", "racial_bonuses", "hp", "hit_dice", "armor_class", "armor_source",
            "speed", "initiative", "proficiency_bonus", "passive_perception", "attacks",
            "spellcasting", "spell_slots", "spells")
    view = {k: dnd[k] for k in keys if k in dnd}
    saves = dnd.get("saving_throws") or {}
    view["saving_throw_proficiencies"] = [
        a for a, v in saves.items() if isinstance(v, dict) and v.get("proficient")
    ]
    skills = dnd.get("skills") or {}
    view["skill_proficiencies"] = {
        k: v.get("source", "") for k, v in skills.items() if isinstance(v, dict)
    }
    build = dnd.get("build") if isinstance(dnd.get("build"), dict) else {}
    if build.get("pool"):
        view["ability_rolls"] = [p["total"] for p in build["pool"]]
    if build.get("hp_rolls"):
        view["hp_rolls"] = build["hp_rolls"]
    return view


async def _grant_equipment(db: AsyncSession, campaign: ImaginaiCampaign, player: ImaginaiEntity) -> list[str]:
    """Equipamento inicial da classe + ouro do antecedente, uma única vez."""
    dnd = _dnd(player)
    build = dnd.get("build") if isinstance(dnd.get("build"), dict) else {}
    if not build or build.get("equipment_granted"):
        return []
    itens, ouro = dnd5e_build.starting_equipment(build)
    taken = set(await db.scalars(
        select(ImaginaiEntity.key).where(ImaginaiEntity.campaign_id == campaign.id)
    ))
    nomes = []
    for item in itens:
        inventory = {"quantity": item.get("quantity", 1), "weight": item.get("weight", 0),
                     "equipped": bool(item.get("equipped")), "slot": item.get("slot")}
        state: dict[str, Any] = {"inventory": inventory, "discovered": True}
        if item.get("kind") == "weapon":
            state["dnd5e"] = {"damage": item["damage"], "damage_type": item["damage_type"],
                              "properties": item.get("properties", [])}
        db.add(ImaginaiEntity(
            campaign_id=campaign.id, user_id=player.user_id, kind="item",
            key=slug(f"item-{item['name']}", taken), name=item["name"], description="",
            owner_entity_id=player.id, state=state,
        ))
        qtd = item.get("quantity", 1)
        nomes.append(item["name"] if qtd == 1 else f"{item['name']} x{qtd}")
    moedas = dict(dnd.get("currencies") or {})
    moedas["gp"] = int(moedas.get("gp", 0) or 0) + ouro
    dnd = {**dnd, "currencies": moedas, "build": {**build, "equipment_granted": True}}
    player.state = {**(player.state or {}), "dnd5e": dnd}
    return nomes + ([f"{ouro} PO"] if ouro else [])


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
            f"O personagem ainda não está pronto (falta: {'; '.join(faltando)}). Peça ao "
            "jogador ou registre com set_character."
        )
    equipamento = await _grant_equipment(db, campaign, player)
    campaign.setup_stage = "play"
    await db.commit()
    return {"stage": "play", "character": character_summary(player),
            "starting_equipment": equipamento, "sheet": sheet_view(_dnd(player))}


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
        "character_sheet": sheet_view(_dnd(player)) if campaign.setup_stage == "character" else None,
        "character_missing": missing_character(player),
        "ability_method": (_dnd(player).get("build") or {}).get("method"),
    }
