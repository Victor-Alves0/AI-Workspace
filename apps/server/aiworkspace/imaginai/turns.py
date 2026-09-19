"""Ponte autoritativa entre o loop de chat e o World Kernel do Imaginai.

O modelo recebe uma única ferramenta. Ele interpreta a linguagem natural, mas
nunca escreve diretamente no estado: consultas, rolagens e commits passam por
serviços determinísticos e transacionais deste módulo.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat.orchestrator import NativeToolOpts
from ..db import SessionLocal
from ..models import (
    ImaginaiCampaign,
    ImaginaiEntity,
    ImaginaiEvent,
    ImaginaiFact,
    ImaginaiKnowledge,
)
from ..schemas.imaginai import ActionRequest
from . import dnd5e_build, encounters, service, setup

_SPELL_SCHEMA = {"type": "object", "properties": {
    "name": {"type": "string"},
    "level": {"type": "integer", "description": "0 = cantrip"},
    "school": {"type": "string"},
    "casting_time": {"type": "string"}, "range": {"type": "string"},
    "duration": {"type": "string"},
    "components": {"type": "string", "description": "e.g. V, S, M (a pinch of ash)"},
    "concentration": {"type": "boolean"}, "ritual": {"type": "boolean"},
    "description": {"type": "string", "description": "Full rules text, as the player would read it."},
    "damage": {"type": "string", "description": "Dice, if it deals damage (e.g. 1d10)."},
    "damage_type": {"type": "string"},
    "save": {"type": "string", "description": "Saving throw ability, if it asks for one."},
    "healing": {"type": "integer"},
}}

_EXPANSION_DESCRIPTION = (
    "New locations/NPCs/factions/lore added to the EXISTING world. Link places with "
    "`connections` (new and old names); an old location may be repeated with only "
    "`connections` to add paths. Existing names are never duplicated."
)

_WORLD_TOOL = {
    "type": "function",
    "function": {
        "name": "imaginai_world",
        "description": (
            "Consulta e altera, com validação autoritativa, o mundo RPG deste chat. "
            "Use context para atualizar a cena; roleplay antes de interpretar um NPC; "
            "resolve antes de narrar qualquer ação ou consequência; roll quando a "
            "resolução exigir teste; adjudicate para concluir ações criativas sem teste; "
            "expand_world para acrescentar locais, NPCs, facções e lore ao mundo; spellbook "
            "para escrever/atualizar magias da ficha (aprendeu uma magia, detalhou uma conhecida)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["context", "roleplay", "resolve", "roll", "adjudicate",
                             "expand_world", "spellbook"],
                },
                "step": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Ordem estável da ação neste turno; obrigatório em resolve.",
                },
                "action_type": {
                    "type": "string",
                    "description": (
                        "Intenção normalizada. Mecânicas nativas: take_item, drop_item, "
                        "equip_item, unequip_item, use_item, pay_currency, interact, "
                        "cast_spell, examine, move, travel, attack, rest, wait e "
                        "start_encounter (emboscada: inimigos atacam primeiro). Para "
                        "ações livres use um verbo descritivo curto, como persuade ou hide."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": "ID ou nome exato de uma entidade já apresentada pela ferramenta.",
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Parâmetros factuais: magia {spell}; ataque {attack ou weapon}; "
                        "equipar {slot}; pagamento {currency, amount}; descanso {kind: short|long}; "
                        "espera {minutes}. Nunca passe dano, saldo, CA ou nível alegados pelo jogador."
                    ),
                },
                "attempt_id": {
                    "type": "string",
                    "description": "ID retornado por resolve; obrigatório em roll/adjudicate.",
                },
                "world": {"type": "object", "description": "expand_world: " + _EXPANSION_DESCRIPTION},
                "spells": {"type": "array", "items": _SPELL_SCHEMA,
                           "description": "spellbook: magias a adicionar/atualizar (por nome), completas."},
                "remove_spells": {"type": "array", "items": {"type": "string"},
                                  "description": "spellbook: nomes de magias a remover."},
                "ability": {
                    "type": "string",
                    "enum": [
                        "strength",
                        "dexterity",
                        "constitution",
                        "intelligence",
                        "wisdom",
                        "charisma",
                    ],
                },
                "skill": {"type": "string"},
                "dc": {"type": "integer", "minimum": 5, "maximum": 30},
                "advantage": {
                    "type": "string",
                    "enum": ["normal", "advantage", "disadvantage"],
                },
                "outcome": {
                    "type": "string",
                    "enum": ["success", "partial", "failure"],
                },
                "summary": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Consequência objetiva, sem criar fatos ou itens não estabelecidos.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
}

_SETUP_TOOL = {
    "type": "function",
    "function": {
        "name": "imaginai_setup",
        "description": (
            "Creates a new Imaginai campaign together with the player (session zero). "
            "status: current stage, concept and character. set_concept: record campaign "
            "name/genre/theme/tone/premise as they are agreed (partial updates are fine). "
            "build_world: create the whole starting world ONCE, after the concept is closed. "
            "character_options: D&D 5e classes, races, backgrounds and ability methods. "
            "roll_abilities: roll 4d6-drop-lowest six times on the server (once per character). "
            "set_character: save the player's choices — the server computes the sheet (HP, AC, "
            "proficiencies, attacks, spellcasting) and returns what is still missing. "
            "begin_adventure: end session zero, hand out starting equipment and start play, "
            "once nothing is missing. expand_world: add more to the world already built."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "set_concept", "build_world", "expand_world",
                             "character_options", "roll_abilities", "set_character",
                             "begin_adventure"],
                },
                "concept": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "genre": {"type": "string", "description": "e.g. dark fantasy, cosmic horror, space opera"},
                        "theme": {"type": "string"},
                        "tone": {"type": "string"},
                        "premise": {"type": "string"},
                    },
                },
                "world": {
                    "type": "object",
                    "properties": {
                        "lore": {"type": "array", "items": {"type": "string"},
                                 "description": "World truths the player may know."},
                        "factions": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"}, "description": {"type": "string"},
                            "secret": {"type": "string"},
                            "visibility": {"type": "string", "enum": ["known", "aware", "hidden"]},
                        }}},
                        "locations": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"}, "description": {"type": "string"},
                            "secret": {"type": "string"},
                            "visibility": {"type": "string", "enum": ["known", "aware", "hidden"]},
                            "connections": {"type": "array", "items": {"type": "string"},
                                            "description": "Names of the locations reachable from here (roads, paths, tunnels)."},
                        }}},
                        "npcs": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string"},
                            "kind": {"type": "string", "enum": ["npc", "creature"]},
                            "description": {"type": "string", "description": "What the player sees."},
                            "persona": {"type": "string", "description": "GM-only: motives, secrets, voice."},
                            "location": {"type": "string", "description": "Name of one of the locations."},
                            "hostile": {"type": "boolean"},
                            "hp": {"type": "integer"}, "ac": {"type": "integer"},
                            "attack_bonus": {"type": "integer"},
                            "damage": {"type": "string", "description": "Dice, e.g. 1d6+2"},
                            "visibility": {"type": "string", "enum": ["known", "aware", "hidden"]},
                        }}},
                        "starting_location": {"type": "string", "description": "Name of the location where play starts (build_world only)."},
                        "opening_scene": {"type": "string"},
                    },
                },
                "character": {
                    "type": "object",
                    "description": "Partial updates accumulate: send only what the player decided now.",
                    "properties": {
                        "name": {"type": "string"},
                        "class": {"type": "string", "description": "A D&D 5e class, e.g. Feiticeiro / Sorcerer."},
                        "level": {"type": "integer"},
                        "race": {"type": "string", "description": "PHB race, or a campaign-specific ancestry."},
                        "race_bonuses": {"type": "object", "description":
                                         "Only for a campaign-specific ancestry (or Half-Elf's +1s): "
                                         "e.g. {\"CAR\": 2, \"CON\": 1}."},
                        "background": {"type": "string", "description": "PHB background, or a custom one."},
                        "background_skills": {"type": "array", "items": {"type": "string"},
                                              "description": "2 skills — only for a custom background."},
                        "ability_method": {"type": "string",
                                           "enum": ["roll", "standard_array", "point_buy", "manual"]},
                        "abilities": {"type": "object", "description":
                                      "Base scores BEFORE racial bonus, e.g. {\"FOR\": 8, \"DES\": 14, "
                                      "\"CON\": 13, \"INT\": 10, \"SAB\": 12, \"CAR\": 15}. Must match the method."},
                        "class_skills": {"type": "array", "items": {"type": "string"},
                                         "description": "Skill proficiencies picked from the class list."},
                        "expertise": {"type": "array", "items": {"type": "string"}, "description": "Rogue only."},
                        "hp_method": {"type": "string", "enum": ["average", "roll"],
                                      "description": "Levels above 1 only (level 1 is always max die + CON)."},
                        "spells": {"type": "array", "items": _SPELL_SCHEMA,
                                   "description": "Write each spell in full (casting time, range, "
                                                  "duration, components, rules text)."},
                        "alignment": {"type": "string"},
                        "backstory": {"type": "string"},
                    },
                },
            },
            "required": ["action"],
        },
    },
}

# expand_world no jogo usa o MESMO formato de mundo da sessão zero
_WORLD_TOOL["function"]["parameters"]["properties"]["world"] = {
    **_SETUP_TOOL["function"]["parameters"]["properties"]["world"],
    "description": _EXPANSION_DESCRIPTION,
}

_SETUP_CONCEPT_PROTOCOL = """## Imaginai — sessão zero: o conceito da campanha
Esta é uma campanha NOVA e você é o mestre. Antes da aventura, vocês a criam juntos.
1. Se a conversa está começando, abra cumprimentando o jogador com calor e personalidade, diga que
   vão começar uma campanha nova e pergunte se ele já tem uma ideia — gênero, tema, clima, um
   personagem ou uma cena em mente. Deixe claro que, se não tiver, você cria tudo.
2. O conceito precisa de nome da campanha, gênero (fantasia sombria, horror cósmico, space opera,
   faroeste...), tema, tom e premissa. O que o jogador não disser, pergunte OU proponha você mesmo —
   no máximo duas perguntas por vez, e nunca trave a conversa por um detalhe. Se ele pedir que você
   decida, decida tudo e apresente.
3. Registre com `imaginai_setup` action=`set_concept` conforme os campos forem definidos.
4. Com o conceito fechado e aceito, chame action=`build_world` UMA vez, criando o mundo inicial:
   lore (verdades que o jogador pode saber), 2–3 facções, 3–6 locais (um deles o local inicial,
   cada um com `connections`: para onde se chega dali — é o grafo do mapa),
   4–8 NPCs — aliados, neutros e ameaças, cada um com `persona` (motivações, segredos, voz) — e
   criaturas hostis com hp/ac/attack_bonus/damage, além da cena de abertura. Personas e segredos
   são só seus: nunca os revele na narração.
5. Depois do build_world, na MESMA resposta, narre a introdução do mundo (cenário, situação, clima —
   sem colocar o personagem em cena ainda) e convide o jogador a criar o personagem: ele pode
   preencher a ficha no painel Personagem → Ficha → Editar, ou contar para você nome, classe, raça,
   antecedente e a história dele.
"""

_SETUP_CHARACTER_PROTOCOL = """## Imaginai — sessão zero: a criação do personagem (D&D 5e)
O mundo já existe (veja o estado abaixo). Agora o jogador cria o personagem, pelas regras do
D&D 5e. Você conduz; o SERVIDOR calcula e rola — você nunca inventa número de ficha.
1. NADA fica salvo sem `imaginai_setup` action=`set_character`. Chame-a no MESMO turno em que o
   jogador decidir algo (nome, classe, raça, antecedente, história...) — só as partes decididas; as
   chamadas se acumulam. Nunca escreva "ficha registrada/registrei" sem o retorno `saved: true`.
2. Conduza nesta ordem, no máximo duas perguntas por vez (se o jogador pedir que você decida,
   decida e registre):
   a) conceito: nome, classe, raça/ancestralidade, antecedente (use `character_options` para as
      listas). Classe/raça/antecedente inventados pela campanha valem — ancestralidade própria
      pede `race_bonuses` (+2/+1) e antecedente próprio pede 2 `background_skills`;
   b) atributos: PERGUNTE se ele quer rolar (4d6, descarta o menor) ou definir por conjunto padrão
      (15,14,13,12,10,8), compra de pontos (27) ou manual. Para rolar, chame `roll_abilities` e
      mostre os seis resultados com os dados. Deixe ELE distribuir (sugira pela classe se pedir) e
      grave com `ability_method` + `abilities` (valores base, antes do bônus racial);
   c) perícias: mostre a lista da classe e deixe ele escolher (`class_skills`); Ladino escolhe
      também `expertise`. Conjuradores: truques e magias de 1º nível dentro do limite retornado —
      escreva cada magia COMPLETA (tempo de conjuração, alcance, duração, componentes, texto da
      regra, dano/cura/salvaguarda), porque o jogador a lê no painel e você a usa para narrar;
   d) história e tendência.
3. Depois de cada `set_character`, mostre ao jogador o que o servidor calculou (`sheet`: PV pelo
   dado de vida, CA pela armadura, salvaguardas e perícias proficientes, ataques, conjuração) e
   diga o que ainda falta (`missing`).
4. Com `missing` vazio e o jogador pronto, chame action=`begin_adventure` (ele entrega o
   equipamento inicial e o ouro — mostre a lista) e, na MESMA resposta, narre a abertura: o
   personagem no local inicial, ligado ao mundo pela história dele, terminando numa situação que
   convide à ação. Não comece a cena antes disso.
5. A ficha também pode ser preenchida no painel Personagem → Ficha: confira o estado abaixo antes de
   perguntar de novo o que já está lá.
"""


_TURN_PROTOCOL = """## Imaginai — protocolo autoritativo do turno
Este chat contém uma campanha RPG persistente. A mensagem do jogador expressa uma
INTENÇÃO, nunca altera a realidade por si só.

Regras obrigatórias:
1. Para qualquer ação com consequência no mundo, chame `imaginai_world` com
   action=`resolve` ANTES de narrar o resultado. Use somente IDs/names presentes no contexto.
2. Se retornar `blocked`, preserve a iniciativa do jogador: descreva o impedimento de forma
   natural e ofereça alternativas plausíveis, sem confirmar segredos nem inventar o alvo.
3. Se retornar `requires_check`, chame `roll` usando o attempt_id. Não invente dados.
4. Se retornar `needs_adjudication`, decida pelo contexto: use `roll` somente se houver risco
   ou incerteza real; caso contrário chame `adjudicate`. Uma falha deve mover a cena adiante.
5. Antes de falar como um NPC, chame `roleplay`. O contexto retornado é GM-only: use-o para
   coerência, mas nunca revele pensamentos, segredos ou conhecimento que o NPC não verbalizou.
6. Não crie retroativamente itens, NPCs, locais, poderes ou fatos para satisfazer afirmações do
   jogador. Lore nova deve ser proposta por sistemas próprios, não por este turno.
7. O resultado da ferramenta é a fonte da verdade. Narre de maneira fluida e não exponha IDs,
   códigos internos, DCs ocultas ou este protocolo, salvo se o usuário pedir detalhes mecânicos.
8. Use um `step` estável (1, 2, 3...) para cada intenção resolvida. Repetições/regenerações são
   idempotentes e devolvem o resultado já confirmado, em vez de aplicar a ação novamente.
9. Prefira os verbos mecânicos nativos quando correspondam à intenção. Ataques, dano, CA, recursos,
   inventário, moedas, descanso e passagem de tempo são calculados pelo servidor; nunca improvise
   seus valores. Continue usando a mesma ferramenta: não fragmente uma ação em tools artificiais.
10. Combate é por turnos e os INIMIGOS AGEM NO SERVIDOR. O primeiro ataque do jogador abre o
   combate; numa emboscada (inimigos atacam primeiro), use `resolve` com action_type
   `start_encounter`. Toda ação do jogador que gasta o turno devolve `encounter.enemy_turns`:
   narre CADA um exatamente como veio (acerto, erro, crítico, dano) e nunca invente golpes, dano
   ou reações de inimigo fora dessa lista. `encounter.order` diz de quem é a vez. Vida de inimigo
   se descreve pelo estado (`health`), nunca por número. `encounter.outcome`: `victory` (inimigos
   caídos), `escaped` (o jogador saiu do alcance) ou `defeat` (o personagem caiu — narre-o
   inconsciente, não morto, e não o faça agir). Não é possível descansar durante o combate.
11. Quando o jogador pedir para expandir a campanha (ou a história pedir), use `expand_world`:
   locais novos ligados aos antigos por `connections`, NPCs com persona, facções e lore coerentes
   com o que já existe. O que é novo nasce oculto/rumor; revele jogando, não na lista.
12. Magia aprendida ou detalhada vai para a ficha com `spellbook`, sempre completa.
"""


async def _setup_turn_tools(
    db: AsyncSession, campaign: ImaginaiCampaign, user_id: uuid.UUID,
    bridge: "ImaginaiTurnBridge",
) -> NativeToolOpts:
    """Turno da sessão zero: só a ferramenta e a instrução da ETAPA atual. O modelo não
    precisa lembrar em que ponto está — nem consegue pular etapas."""
    estado: dict[str, Any] = {"setup": await setup.status(db, campaign)}
    if campaign.setup_stage == "character":
        # o mundo já existe: a abertura precisa dos nomes reais do local e de quem está lá
        estado["scene"] = await scene_context(db, campaign)
        protocolo = _SETUP_CHARACTER_PROTOCOL
    else:
        protocolo = _SETUP_CONCEPT_PROTOCOL
    from .. import sound_effects

    sons = await sound_effects.instruction_if_available(db, user_id)
    if sons:
        protocolo = f"{protocolo}\n{sons}"
    estado_json = json.dumps(estado, ensure_ascii=False, default=str)
    return NativeToolOpts(
        specs=[_SETUP_TOOL],
        prompt=f"{protocolo}\n\n## Estado da sessão zero\n```json\n{estado_json}\n```",
        run=bridge.run,
    )


def _trim(value: Any, limit: int = 500) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {str(key)[:80]: _trim(item, limit) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return [_trim(item, limit) for item in value[:40]]
    return value


async def _player(db: AsyncSession, campaign: ImaginaiCampaign) -> ImaginaiEntity:
    player = await db.scalar(
        select(ImaginaiEntity).where(
            ImaginaiEntity.campaign_id == campaign.id,
            ImaginaiEntity.kind == "character",
            ImaginaiEntity.key == "player",
            ImaginaiEntity.active.is_(True),
        )
    )
    if player is None:
        raise service.WorldNotFoundError("Personagem não encontrado")
    return player


def _discovered_ids(player: ImaginaiEntity) -> set[str]:
    state = player.state if isinstance(player.state, dict) else {}
    dnd = state.get("dnd5e", state)
    raw = dnd.get("discovered_entity_ids", []) if isinstance(dnd, dict) else []
    return {str(item) for item in raw if item}


def _entity_known(player: ImaginaiEntity, entity: ImaginaiEntity) -> bool:
    if entity.id == player.id or entity.owner_entity_id == player.id:
        return True
    state = entity.state if isinstance(entity.state, dict) else {}
    discovered = _discovered_ids(player)
    if state.get("hidden") and not (state.get("discovered") or str(entity.id) in discovered):
        return False
    return bool(
        entity.location_id == player.location_id
        or state.get("discovered")
        or str(entity.id) in discovered
        or str(state.get("discovery", "")).casefold() in {"aware", "known"}
    )


def _entity_accessible(player: ImaginaiEntity, entity: ImaginaiEntity) -> bool:
    if entity.id == player.id or entity.owner_entity_id == player.id:
        return True
    if entity.location_id != player.location_id:
        return False
    state = entity.state if isinstance(entity.state, dict) else {}
    return not (
        state.get("hidden")
        and not (state.get("discovered") or str(entity.id) in _discovered_ids(player))
    )


def _public_entity(entity: ImaginaiEntity, *, include_state: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(entity.id),
        "kind": entity.kind,
        "name": entity.name,
        "description": _trim(entity.description, 300),
        "location_id": str(entity.location_id) if entity.location_id else None,
        "owner_entity_id": str(entity.owner_entity_id) if entity.owner_entity_id else None,
    }
    if include_state:
        data["state"] = _trim(entity.state or {}, 500)
    elif entity.kind == "item":
        state = entity.state if isinstance(entity.state, dict) else {}
        inventory = state.get("inventory", state)
        data["inventory"] = _trim(inventory, 200) if isinstance(inventory, dict) else {}
    return data


def _player_sheet(player: ImaginaiEntity) -> dict[str, Any]:
    state = player.state if isinstance(player.state, dict) else {}
    dnd = state.get("dnd5e", state)
    dnd = dnd if isinstance(dnd, dict) else {}
    allowed = (
        "class",
        "level",
        "hp",
        "armor_class",
        "initiative",
        "speed",
        "proficiency_bonus",
        "attributes",
        "skills",
        "saving_throws",
        "spells",
        "spell_slots",
        "attacks",
        "equipment",
        "currencies",
        "conditions",
        "ancestry",
        "background",
        "alignment",
    )
    sheet: dict[str, Any] = {"dnd5e": _trim({key: dnd[key] for key in allowed if key in dnd}, 240)}
    if dnd.get("backstory"):
        # a história é longa de propósito: é dela que o narrador tira ganchos e laços
        sheet["backstory"] = _trim(str(dnd["backstory"]), 3000)
    return sheet


async def scene_context(db: AsyncSession, campaign: ImaginaiCampaign) -> dict[str, Any]:
    player = await _player(db, campaign)
    location = await db.get(ImaginaiEntity, player.location_id) if player.location_id else None
    entities = list(
        await db.scalars(
            select(ImaginaiEntity).where(
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.active.is_(True),
                ImaginaiEntity.id != player.id,
            )
        )
    )
    visible = [entity for entity in entities if _entity_accessible(player, entity)]
    known_remote = [
        entity
        for entity in entities
        if not _entity_accessible(player, entity) and _entity_known(player, entity)
    ]
    known_rows = list(
        await db.execute(
            select(ImaginaiFact, ImaginaiKnowledge)
            .join(ImaginaiKnowledge, ImaginaiKnowledge.fact_id == ImaginaiFact.id)
            .where(
                ImaginaiKnowledge.campaign_id == campaign.id,
                ImaginaiKnowledge.knower_entity_id == player.id,
                ImaginaiKnowledge.believes_true.is_(True),
            )
            .order_by(ImaginaiKnowledge.learned_at_tick.desc())
            .limit(24)
        )
    )
    events = list(
        await db.scalars(
            select(ImaginaiEvent)
            .where(
                ImaginaiEvent.campaign_id == campaign.id,
                ImaginaiEvent.visibility.in_(["public", "party"]),
            )
            .order_by(ImaginaiEvent.sequence.desc())
            .limit(10)
        )
    )
    return {
        "campaign": {
            "id": str(campaign.id),
            "name": campaign.name,
            "system": campaign.system_key,
            "world_tick": campaign.world_tick,
            "settings": _trim(campaign.settings or {}, 300),
        },
        "player": {**_public_entity(player), "state": _player_sheet(player)},
        "location": _public_entity(location)
        if location and location.campaign_id == campaign.id
        else None,
        "visible_entities": [_public_entity(entity) for entity in visible[:30]],
        "known_entities_elsewhere": [
            {
                **_public_entity(entity),
                "description": (
                    "[detalhes ainda não descobertos]"
                    if str((entity.state or {}).get("discovery", "")).casefold() == "aware"
                    else _trim(entity.description, 300)
                ),
            }
            for entity in known_remote[:20]
        ],
        "known_facts": [
            {
                "subject_id": str(fact.subject_id) if fact.subject_id else None,
                "predicate": fact.predicate,
                "value": _trim(fact.value, 240),
                "confidence": knowledge.confidence,
            }
            for fact, knowledge in known_rows
        ],
        # combate em andamento: de quem é a vez e como cada um está. O narrador lê
        # daqui a rodada e a ordem, em vez de adivinhar pela conversa.
        "encounter": await encounters.summary(db, campaign, player),
        "recent_events": [
            {
                "sequence": event.sequence,
                "world_tick": event.world_tick,
                "type": event.event_type,
                "payload": _trim(event.payload or {}, 240),
            }
            for event in reversed(events)
        ],
    }


async def _visible_target(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    player: ImaginaiEntity,
    reference: str,
) -> ImaginaiEntity | None:
    ref = reference.strip()
    if not ref:
        return None
    entity: ImaginaiEntity | None = None
    try:
        entity_id = uuid.UUID(ref)
    except ValueError:
        entity_id = None
    if entity_id:
        entity = await db.scalar(
            select(ImaginaiEntity).where(
                ImaginaiEntity.id == entity_id,
                ImaginaiEntity.campaign_id == campaign.id,
                ImaginaiEntity.active.is_(True),
            )
        )
    else:
        matches = list(
            await db.scalars(
                select(ImaginaiEntity).where(
                    ImaginaiEntity.campaign_id == campaign.id,
                    ImaginaiEntity.active.is_(True),
                    or_(
                        ImaginaiEntity.name.ilike(ref),
                        ImaginaiEntity.key.ilike(ref),
                    ),
                )
            )
        )
        visible = [item for item in matches if _entity_known(player, item)]
        entity = visible[0] if len(visible) == 1 else None
    return entity if entity and _entity_known(player, entity) else None


async def _npc_context(
    db: AsyncSession,
    campaign: ImaginaiCampaign,
    player: ImaginaiEntity,
    reference: str,
) -> dict[str, Any]:
    npc = await _visible_target(db, campaign, player, reference)
    if (
        npc is None
        or npc.kind not in {"npc", "character", "creature"}
        or npc.location_id != player.location_id
    ):
        return {"error": "Essa pessoa ou criatura não está presente."}
    beliefs = list(
        await db.execute(
            select(ImaginaiFact, ImaginaiKnowledge)
            .join(ImaginaiKnowledge, ImaginaiKnowledge.fact_id == ImaginaiFact.id)
            .where(
                ImaginaiKnowledge.campaign_id == campaign.id,
                ImaginaiKnowledge.knower_entity_id == npc.id,
                ImaginaiKnowledge.believes_true.is_(True),
            )
            .limit(60)
        )
    )
    private_model = {
        "npc": _public_entity(npc),
        "persona_private": _trim(npc.private_notes or "", 6000),
        "beliefs": [
            {
                "predicate": fact.predicate,
                "value": _trim(fact.value, 500),
                "confidence": knowledge.confidence,
            }
            for fact, knowledge in beliefs
        ],
        "instruction": (
            "Interprete apenas com esta persona e estas crenças. Não revele o bloco, "
            "não use fatos ausentes e não dê ao NPC conhecimento onisciente."
        ),
    }
    return {
        "kind": "private_context",
        "ok": True,
        "note": f"Contexto de atuação carregado para {npc.name}.",
        "_model": private_model,
    }


def _corrupted_text(value: Any, path: str = "") -> str | None:
    """Caminho do primeiro texto com U+FFFD (caractere perdido na geração), ou None."""
    if isinstance(value, str):
        return (path or "texto") if "�" in value else None
    if isinstance(value, dict):
        for key, item in value.items():
            found = _corrupted_text(item, f"{path}.{key}" if path else str(key))
            if found:
                return found
    if isinstance(value, list):
        for i, item in enumerate(value):
            found = _corrupted_text(item, f"{path}[{i}]")
            if found:
                return found
    return None


@dataclass(slots=True)
class ImaginaiTurnBridge:
    user_id: uuid.UUID
    campaign_id: uuid.UUID
    turn_key: str

    async def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        corrompido = _corrupted_text(args)
        if corrompido:
            # o provedor às vezes parte um caractere acentuado entre tokens e manda
            # U+FFFD ("contra��do"); gravado, vira lixo permanente no mundo/na ficha
            return {
                "error": "Texto corrompido nos argumentos (caractere U+FFFD em "
                         f"{corrompido!r}). Reenvie a MESMA chamada com a acentuação correta."
            }
        if name == "imaginai_setup":
            return await _setup_action(self, args)
        if name != "imaginai_world":
            return {"error": "Ferramenta de mundo desconhecida"}
        action = str(args.get("action") or "").strip()
        async with SessionLocal() as db:
            campaign = await service.owned_campaign(db, self.user_id, self.campaign_id)
            player = await _player(db, campaign)
            if action == "context":
                return {"kind": "imaginai_context", "scene": await scene_context(db, campaign)}
            if action == "expand_world":
                locked = await service.owned_campaign(db, self.user_id, self.campaign_id, lock=True)
                try:
                    result = await setup.expand_world(db, locked, self.user_id, args.get("world") or {})
                except setup.SetupError as exc:
                    return {"kind": "imaginai_expansion", "error": str(exc)}
                return {"kind": "imaginai_expansion", **result}
            if action == "spellbook":
                locked = await service.owned_campaign(db, self.user_id, self.campaign_id, lock=True)
                result = await service.write_spells(
                    db, locked, list(args.get("spells") or []),
                    remove=list(args.get("remove_spells") or []),
                )
                return {"kind": "imaginai_spellbook", "spells": [s["name"] for s in result["spells"]]}
            if action == "roleplay":
                return await _npc_context(db, campaign, player, str(args.get("target") or ""))
            if action == "resolve":
                step = int(args.get("step") or 0)
                if not 1 <= step <= 20:
                    raise ValueError("step entre 1 e 20 é obrigatório em resolve")
                action_type = str(args.get("action_type") or "").strip().lower()
                if not action_type:
                    raise ValueError("action_type é obrigatório em resolve")
                target = await _visible_target(db, campaign, player, str(args.get("target") or ""))
                parameters = args.get("parameters")
                normalized_parameters = dict(parameters) if isinstance(parameters, dict) else {}
                normalized_parameters["_target_requested"] = bool(
                    str(args.get("target") or "").strip()
                )
                body = ActionRequest(
                    actor_id=player.id,
                    action_type=action_type,
                    target_id=target.id if target else None,
                    parameters=normalized_parameters,
                )
                result = await service.resolve_action(
                    db,
                    self.user_id,
                    campaign.id,
                    body,
                    turn_key=self.turn_key,
                    turn_step=step,
                )
                if result.get("status") == "resolved":
                    result["scene"] = await scene_context(db, campaign)
                return {"kind": "imaginai_resolution", **result}
            if action == "roll":
                result = await service.resolve_check(
                    db,
                    self.user_id,
                    campaign.id,
                    uuid.UUID(str(args.get("attempt_id") or "")),
                    ability=str(args.get("ability") or ""),
                    skill=str(args.get("skill") or "").strip() or None,
                    dc=int(args.get("dc") or 10),
                    advantage=str(args.get("advantage") or "normal"),
                )
                return {"kind": "imaginai_roll", **result}
            if action == "adjudicate":
                result = await service.adjudicate_attempt(
                    db,
                    self.user_id,
                    campaign.id,
                    uuid.UUID(str(args.get("attempt_id") or "")),
                    outcome=str(args.get("outcome") or "partial"),
                    summary=str(args.get("summary") or ""),
                )
                return {"kind": "imaginai_adjudication", **result}
        return {"error": "Ação de mundo inválida"}


async def _setup_action(bridge: "ImaginaiTurnBridge", args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    async with SessionLocal() as db:
        campaign = await service.owned_campaign(db, bridge.user_id, bridge.campaign_id, lock=True)
        try:
            if action == "status":
                result = await setup.status(db, campaign)
            elif action == "set_concept":
                result = await setup.set_concept(db, campaign, args.get("concept") or {})
            elif action == "build_world":
                result = await setup.build_world(db, campaign, bridge.user_id, args.get("world") or {})
            elif action == "expand_world":
                result = await setup.expand_world(db, campaign, bridge.user_id, args.get("world") or {})
            elif action == "character_options":
                result = {"stage": campaign.setup_stage, **dnd5e_build.options()}
            elif action == "roll_abilities":
                result = await setup.roll_abilities(db, campaign)
            elif action == "set_character":
                result = await setup.set_character(db, campaign, args.get("character") or {})
            elif action == "begin_adventure":
                result = await setup.begin_adventure(db, campaign)
                result["scene"] = await scene_context(db, campaign)
            else:
                return {"kind": "imaginai_setup", "error": "Ação de sessão zero inválida"}
        except setup.SetupError as exc:
            # erro de etapa volta ao narrador como orientação, não como falha do turno
            return {"kind": "imaginai_setup", "error": str(exc)}
        return {"kind": "imaginai_setup", **result}


async def prepare_turn_tools(
    db: AsyncSession,
    user_id: uuid.UUID,
    chat_id: uuid.UUID,
    turn_key: str,
) -> NativeToolOpts | None:
    campaign = await db.scalar(
        select(ImaginaiCampaign).where(
            ImaginaiCampaign.chat_id == chat_id,
            ImaginaiCampaign.user_id == user_id,
            ImaginaiCampaign.status == "active",
        )
    )
    if campaign is None:
        return None
    bridge = ImaginaiTurnBridge(user_id=user_id, campaign_id=campaign.id, turn_key=turn_key)
    if campaign.setup_stage in ("concept", "character"):
        return await _setup_turn_tools(db, campaign, user_id, bridge)
    context = await scene_context(db, campaign)
    context_json = json.dumps(context, ensure_ascii=False, default=str)
    # a narração de RPG é o caso de uso dos efeitos sonoros: no Imaginai eles valem
    # sem precisar ligar a capacidade no modelo (desde que haja ElevenLabs para tocar)
    from .. import sound_effects

    sons = await sound_effects.instruction_if_available(db, user_id)
    protocolo = f"{_TURN_PROTOCOL}\n\n{sons}" if sons else _TURN_PROTOCOL
    return NativeToolOpts(
        specs=[_WORLD_TOOL],
        prompt=f"{protocolo}\n\n## Estado conhecido no início do turno\n```json\n{context_json}\n```",
        run=bridge.run,
    )
