"""Preparação de turno — helpers compartilhados pelas rotas de chat e pelos
outros fluxos (canais WhatsApp/Telegram, automações, playground).

Tudo aqui é "como montar um turno": resolução de provedor/modelo/skills/SIFT,
capacidades do modelo (visão/OCR/áudio/imagem), memória, conhecimento, guardas,
subagentes, anexos e os builders dos grupos de opções do run_turn. As ROTAS
ficam nos módulos routes*.py; este módulo não define endpoints.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import extraction
from ..config import get_settings
from ..db import SessionLocal
from ..integrations import chatgpt_service, ollama_service, providers_service
from ..models import Artifact, Chat, KnowledgeBase, KnowledgeDoc, Message, ModelConfig, Skill, User
from ..secrets_service import IMAGEGEN_KEY, OPENROUTER_KEY, VOICE_KEY, get_secret
from ..tools.loader import get_sift_for_user, tool_config
from ..usage_service import usage_event_from_record
from . import artifacts as artifacts_service
from . import attachment_context
from . import generation
from .orchestrator import MediaOpts, MemoryOpts, SubagentOpts, TurnSession, run_turn
from .subagent_team import MAX_AGENTS, MAX_CONCURRENCY, SubagentPool

logger = logging.getLogger(__name__)

# último recurso do Audio Router: modelo multimodal de áudio via OpenRouter (a
# chave do turno já existe) — mesmo exemplo que a UI do ModelEditor sugere
_AUDIO_FALLBACK_MODEL = "google/gemini-2.5-flash"

# Preferência escolhida no composer para ESTE chat. Ela fica nos params do Chat
# (e não no ModelConfig) para que mudar Médio/Alto numa conversa não altere todas
# as outras que usam o mesmo preset. O nome reservado nunca é enviado ao provider.
CHAT_REASONING_EFFORT_PARAM = "_chat_reasoning_effort"
_REASONING_EFFORTS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})


def _params_with_chat_reasoning(
    base_params: dict | None, chat_params: dict | None
) -> dict:
    """Aplica somente a preferência de reasoning específica da conversa.

    Os demais ``chat_params`` podem ser um snapshot antigo de ModelConfig e não
    devem sobrepor o preset atual. A chave reservada é removida em todos os casos
    para nunca vazar como parâmetro desconhecido à API do modelo.
    """
    params = dict(base_params) if isinstance(base_params, dict) else {}
    params.pop(CHAT_REASONING_EFFORT_PARAM, None)
    overrides = chat_params if isinstance(chat_params, dict) else {}
    raw_effort = overrides.get(CHAT_REASONING_EFFORT_PARAM)
    effort = raw_effort if isinstance(raw_effort, str) and raw_effort in _REASONING_EFFORTS else None
    if effort == "off":
        params.pop("reasoning", None)
    elif effort is not None:
        params["reasoning"] = {"effort": effort}
    return params


def _tz_from_header(x_timezone: str | None = Header(default=None)) -> str:
    """Fuso IANA do navegador (header `X-Timezone`, ex.: America/Sao_Paulo),
    validado. Vazio se ausente/ inválido → o servidor cai em UTC. Alimenta o
    contexto temporal do modelo e a criação de lembretes/eventos de agenda."""
    tz = (x_timezone or "").strip()
    if tz:
        try:
            ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            tz = ""
    return tz


def _remember_tz(user: User, user_tz: str) -> None:
    """Guarda o fuso do navegador no profile (persiste no commit que o turno já
    faz). É daí que os CANAIS (WhatsApp/Telegram/Discord) tiram o fuso — lá não há
    navegador, e sem isso o modelo recebia a hora em UTC como se fosse a local
    (dizia "já passou das 20h" às 17h de Brasília).

    Se o usuário fixou o fuso à mão (Configurações → Geral → `tz_manual`), NÃO
    sobrescreve: a escolha manual vence o autodetectado do navegador."""
    prof = user.profile or {}
    if prof.get("tz_manual"):
        return
    if user_tz and prof.get("timezone") != user_tz:
        # reatribuição (não mutação): JSONB só marca dirty com objeto novo
        user.profile = {**prof, "timezone": user_tz}


def _profile_tz(user: User) -> str:
    """Fuso IANA salvo no profile (via `_remember_tz` ou escolha manual) — o que os
    canais usam (lá não há navegador que informe o fuso)."""
    return str((user.profile or {}).get("timezone") or "")


def _session_tz(user: User, header_tz: str) -> str:
    """Fuso efetivo do turno web: a escolha manual do usuário vence o header do
    navegador; senão usa o que o navegador informou."""
    prof = user.profile or {}
    if prof.get("tz_manual") and prof.get("timezone"):
        return str(prof["timezone"])
    return header_tz


async def _get_owned_chat(db: AsyncSession, chat_id: uuid.UUID, user: User) -> Chat:
    chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat não encontrado")
    return chat


async def _get_model_config(
    db: AsyncSession, model_config_id, user: User
) -> ModelConfig | None:
    """Modelo personalizado do usuário (ou None). Define quais ferramentas o chat usa."""
    if not model_config_id:
        return None
    try:
        mid = model_config_id if isinstance(model_config_id, uuid.UUID) else uuid.UUID(str(model_config_id))
    except (ValueError, AttributeError):
        return None
    mc = await db.get(ModelConfig, mid)
    if mc is None or mc.user_id != user.id:
        return None
    return mc


def _effective_chat_model(
    chat: Chat, model_config: ModelConfig | None
) -> tuple[str, str | None, dict]:
    """Configuração que realmente governa um turno de chat.

    ``Chat.model``, ``system_prompt`` e ``params`` existiam antes dos modelos
    customizados e continuam sendo o fallback para chats comuns. Para um chat
    vinculado a um ``ModelConfig``, porém, eles são apenas um snapshot legado do
    momento da seleção. Usá-los no runtime fazia o chat ficar preso ao prompt,
    aos parâmetros e até ao modelo-base antigos após editar o preset.

    O UUID em ``model_config_id`` é a identidade estável; a configuração atual
    desse registro é a fonte de verdade em todo novo turno.
    """
    if model_config is not None and model_config.base_model:
        return (
            model_config.base_model,
            model_config.system_prompt,
            _params_with_chat_reasoning(model_config.params, chat.params),
        )
    return chat.model, chat.system_prompt, _params_with_chat_reasoning(chat.params, chat.params)


def _usage_record(usage: dict | None, model: str, model_config: ModelConfig | None) -> dict:
    """Monta o registro ponta-a-ponta de uma mensagem: origem + tokens + custo."""
    u = usage or {}
    rec = {
        "model": model,
        "model_config_id": str(model_config.id) if model_config else None,
        "model_name": model_config.name if model_config else model,
        # separação das FONTES no ledger: local (Ollama) ≠ assinatura (ChatGPT) ≠ API
        "provider": ("ollama" if (model or "").startswith("ollama/")
                     else "chatgpt" if (model or "").startswith("codex/")
                     else "openrouter"),
        "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(u.get("completion_tokens", 0) or 0),
        "total_tokens": int(u.get("total_tokens", 0) or 0),
        "reasoning_tokens": int(u.get("reasoning_tokens", 0) or 0),
        "cached_tokens": int(u.get("cached_tokens", 0) or 0),
        "cost": float(u.get("cost", 0.0) or 0.0),
        # chamadas ao modelo no turno (1 + uma por passo de ferramenta)
        "llm_calls": int(u.get("llm_calls", 0) or 0),
    }
    # tamanho REAL do contexto (prompt da 1ª chamada). Não era copiado: depois de
    # recarregar, o medidor caía numa estimativa e a auto-compactação lia o
    # prompt_tokens — a SOMA das chamadas —, achando o contexto N× maior.
    if u.get("context_tokens"):
        rec["context_tokens"] = int(u["context_tokens"])
    # TODOS os detalhamentos do turno (ver orchestrator._finalize_usage). Copiados por
    # sufixo em vez de um a um: a lista explícita já tinha esquecido o `extra_breakdown`,
    # e o painel de uso mostrava as sub-linhas de "Instruções extras" sempre vazias.
    for k, v in u.items():
        if k.endswith("_breakdown") and isinstance(v, dict):
            rec[k] = v
    return rec


async def _load_skills(
    db: AsyncSession,
    user: User,
    model_config: ModelConfig | None,
    extra_ids: list | None = None,
) -> list[dict]:
    """Resolve as skills equipadas no modelo + invocadas ad-hoc ($) em documentos
    {slug, name, description, content} — só as habilitadas e do próprio usuário."""
    raw: list = []
    if model_config and model_config.skill_ids:
        raw.extend(model_config.skill_ids)
    if extra_ids:
        raw.extend(extra_ids)
    seen: set[uuid.UUID] = set()
    uids: list[uuid.UUID] = []
    for x in raw:
        try:
            u = x if isinstance(x, uuid.UUID) else uuid.UUID(str(x))
        except (ValueError, AttributeError, TypeError):
            continue
        if u not in seen:
            seen.add(u)
            uids.append(u)
    if not uids:
        return []
    rows = await db.scalars(
        select(Skill).where(Skill.user_id == user.id, Skill.id.in_(uids))
    )
    return [
        {
            "slug": s.slug,
            "name": s.name,
            "description": s.description or "",
            "content": s.content or "",
            "files": list(s.files or []),
        }
        for s in rows
        if s.enabled
    ]


async def _resolve_provider(db: AsyncSession, user: User, model: str) -> tuple[str, str | None]:
    """Resolve o provedor a partir do id do modelo → (api_key, base_url).
    Modelo `ollama/*` → servidor Ollama local do usuário (OpenAI-compat, sem chave);
    `codex/*` → assinatura ChatGPT conectada (a "api_key" vira o sentinela
    `codex:<uid>` — o token real é buscado/renovado no adaptador por chamada);
    senão → OpenRouter (chave do usuário). base_url=None significa OpenRouter."""
    if (model or "").startswith(ollama_service.MODEL_PREFIX):
        base = await ollama_service.get_base_url(db, user.id)
        if not base:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Ollama não configurado. Ative em Configurações → Conexões → Ollama, ou escolha outro modelo.",
            )
        return "ollama", base.rstrip("/") + "/v1"
    if (model or "").startswith(chatgpt_service.MODEL_PREFIX):
        if not await chatgpt_service.is_connected(db, str(user.id)):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "ChatGPT não conectado. Conecte em Configurações → Conexões → Assinaturas, ou escolha outro modelo.",
            )
        return f"codex:{user.id}", None
    if (model or "").startswith(providers_service.MODEL_PREFIX):
        prov = await providers_service.resolve_for_model(db, user.id, model)
        if not prov:
            slug = providers_service.slug_from_model(model) or "?"
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Provedor '{slug}' não configurado (falta chave/URL ou está desligado). "
                "Ajuste em Configurações → Conexões → Provedores, ou escolha outro modelo.",
            )
        return prov["api_key"], prov["base_url"]
    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    if not api_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Configure sua chave do OpenRouter primeiro"
        )
    return api_key, None


async def _prepare_turn(db: AsyncSession, user: User, chat: Chat):
    """Prepara um turno e devolve também seus valores efetivos de runtime.

    A tupla termina em ``(model, system_prompt, params)`` para que os caminhos
    de regenerar, continuar e retomar não voltem a usar snapshots do ``Chat``.
    """
    model_config = await _get_model_config(db, chat.model_config_id, user)
    model, system_prompt, params = _effective_chat_model(chat, model_config)
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione um modelo no chat")
    api_key, base_url = await _resolve_provider(db, user, model)
    sift = await get_sift_for_user(
        db, user.id, model_config,
        codespace_project_id=str(chat.project_id) if chat.project_id else None,
    )
    skills = await _load_skills(db, user, model_config)
    return api_key, base_url, model_config, sift, skills, model, system_prompt, params


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _code_mode(model_config: ModelConfig | None) -> bool:
    """Code mode da SIFT vale só quando o mestre (tools_enabled) está ligado — e
    quando o operador não desligou o off-switch global (allow_code_mode)."""
    if not get_settings().allow_code_mode:
        return False
    return bool(model_config and model_config.tools_enabled and model_config.code_mode)


def _use_context(model_config: ModelConfig | None) -> bool:
    """Capacidade "Contexto do Chat": default LIGADA. Só desliga quando o modelo
    tem capabilities.chat_context explicitamente False (turno sem histórico)."""
    if model_config is None:
        return True
    return (model_config.capabilities or {}).get("chat_context", True) is not False


def _has_vision(model_config: ModelConfig | None) -> bool:
    """Capacidade "Visão": o modelo recebe as imagens diretamente (multimodal)."""
    return bool(model_config and (model_config.capabilities or {}).get("vision"))


def _token_warn(model_config: ModelConfig | None, user: User) -> int:
    """Limite de aviso de uso (tokens/turno) do PERFIL (Configurações → Conta); 0 =
    desligado. Só avisa, não bloqueia. O override por modelo saiu da UI (19/09) — um
    valor antigo gravado no modelo não pode continuar valendo sem controle visível."""
    del model_config
    try:
        return int((user.profile or {}).get("token_warn") or 0)
    except (TypeError, ValueError):
        return 0


def _flag_budget(rec: dict, model_config: ModelConfig | None, user: User) -> None:
    """Marca o registro de uso quando o turno passou do limite configurado."""
    warn = _token_warn(model_config, user)
    if warn and (rec.get("total_tokens") or 0) > warn:
        rec["over_budget"] = warn


def _final_message_fields(collected: dict) -> tuple[str, dict | None]:
    """Resolve o conteúdo e o raciocínio a PERSISTIR ao fim de um turno, de modo que
    a resposta NÃO suma quando o stream falha no meio (erro/moderação) e o `done`
    nunca chega. Cobre: conteúdo completo, parcial (streamed), raciocínio parcial e
    erro — sempre deixando um vestígio visível em vez de a mensagem desaparecer."""
    content = collected.get("content") or (collected.get("streamed") or "").strip()
    reasoning = collected.get("reasoning")
    if not reasoning and (collected.get("reasoning_streamed") or "").strip():
        reasoning = {"text": collected["reasoning_streamed"].strip()}
    err = collected.get("error")
    if err:
        note = f"⚠️ A resposta foi interrompida: {err}"
        content = f"{content}\n\n{note}" if content else note
    elif not content and reasoning:
        content = "⚠️ O modelo não retornou uma resposta final (apenas o raciocínio acima)."
    return content, reasoning


# padrão de memória (novos chats): OPT-IN (desligada) — igual ao memory_routes.DEFAULT_MEMORY
_DEFAULT_MEMORY = {"enabled": False, "write": "global",
                   "read": {"global": True, "model": True, "chat": True, "project": True}}


def _mem_project(chat: Chat | None) -> str | None:
    """Id do 'projeto' p/ a memória compartilhada = a pasta do chat (folder_id).
    None quando o chat não está numa pasta (aí o escopo 'projeto' não se aplica)."""
    fid = getattr(chat, "folder_id", None) if chat is not None else None
    return str(fid) if fid else None


def _user_profile_dict(user: User) -> dict[str, Any]:
    """Dados da conta do usuário expostos à ferramenta `user.profile.get` (nome,
    sobre, gênero, nascimento, e-mail, idioma). Só o que estiver preenchido."""
    prof = user.profile or {}
    return {
        "name": (prof.get("name") or "").strip(),
        "about": (prof.get("about") or "").strip(),
        "gender": (prof.get("gender") or "").strip(),
        "birthdate": (prof.get("birthdate") or "").strip(),
        "email": user.email or "",
        "language": (prof.get("language") or "").strip(),
    }


def _artifacts_enabled(user: User) -> bool:
    """Toggle "Artefatos" (Configurações → Interface → Chat). Padrão: ligado."""
    iface = (user.profile or {}).get("interface")
    iface = iface if isinstance(iface, dict) else {}
    return bool(iface.get("artifacts", True))


async def _artifacts_extra(
    db: AsyncSession, chat_id: uuid.UUID, user: User, model_config: ModelConfig | None,
) -> str | None:
    """Bloco de system prompt dos Artefatos: instruções de uso + conteúdo ATUAL
    dos artefatos do chat (o modelo vê edições manuais do usuário).

    Injeta SÓ quando faz sentido (economia de ~400 tokens/turno): o modelo tem a
    capacidade `artifacts` OU o chat já tem artefatos (precisa do estado atual p/
    editar). Sem nenhum dos dois, não injeta nada."""
    if not _artifacts_enabled(user):
        return None
    rows = list(await db.scalars(select(Artifact).where(Artifact.chat_id == chat_id)))
    cap_on = _has_capability(model_config, "artifacts", default=False)
    if not cap_on and not rows:
        return None
    return artifacts_service.system_block(rows)


async def _artifacts_kwargs(
    db: AsyncSession, chat_id: uuid.UUID, user: User, arts_on: bool,
    model_config: ModelConfig | None,
) -> dict:
    """Blocos de sistema do chat p/ o run_turn (`extra_system`) + o rótulo de cada um
    no detalhamento de uso (painel Extenso): Artefatos e Efeitos sonoros. Cada bloco só
    entra quando a capacidade está ligada — economia de tokens por turno."""
    blocks: list[str] = []
    breakdown: dict[str, int] = {}
    if arts_on:
        txt = await _artifacts_extra(db, chat_id, user, model_config)
        if txt:
            blocks.append(txt)
            breakdown["artifacts"] = len(txt)
    if _has_capability(model_config, "sound_effects", default=False):
        from .. import sound_effects

        # sem ElevenLabs ligada o botão não tocaria: nem pede o marcador ao modelo
        snd = await sound_effects.instruction_if_available(db, user.id)
        if snd:
            blocks.append(snd)
            breakdown["sound_effects"] = len(snd)
    if not blocks:
        return {}
    return {"extra_system": "\n\n".join(blocks), "extra_breakdown": breakdown}


# --------------------------------------------------------------------------- #
# Builders dos grupos de opções do run_turn (TurnSession/MemoryOpts/MediaOpts/
# SubagentOpts) — cada rota monta os grupos com 1 linha em vez de ~15 kwargs.
# --------------------------------------------------------------------------- #

def _memory_opts(chat: Chat, model_config: ModelConfig | None, user: User) -> MemoryOpts:
    """Memória efetiva do turno (chat → modelo → perfil) já no formato do run_turn."""
    read, write, review = _resolve_memory(chat, model_config, user)
    return MemoryOpts(
        read=read, write=write, review=review,
        banks=_mem_banks(chat, model_config, user), project=_mem_project(chat),
    )


async def _media_opts(
    db: AsyncSession, user: User, model_config: ModelConfig | None,
    attachments: list[dict] | None = None,
) -> MediaOpts:
    """Anexos + roteadores de mídia conforme as capacidades do modelo."""
    ocr_on, ocr_eng, ocr_lang = _ocr_prefs(model_config)
    return MediaOpts(
        attachments=attachments or None,
        vision=_has_vision(model_config),
        audio=bool(model_config and (model_config.capabilities or {}).get("audio")),
        vision_router_model=_vision_router_model(model_config),
        audio_router=await _audio_router_config(db, user, model_config),
        ocr=ocr_on, ocr_engine=ocr_eng, ocr_lang=ocr_lang,
        genimage=await _genimage_config(db, user, model_config),
        image_output=_image_output(model_config),
    )


def _subagent_opts(sub_specs: list[dict], sub_conf: dict, sub_runner) -> SubagentOpts | None:
    if sub_runner is None or not (sub_specs or sub_conf.get("adhoc")):
        return None
    return SubagentOpts(
        agents=sub_specs, run=sub_runner,
        mode=sub_conf.get("mode", "parallel"),
        max_calls=sub_conf.get("max_calls", 4),
        pass_context=sub_conf.get("pass_context", False),
        worker_memory=sub_conf.get("worker_memory", False),
        adhoc=bool(sub_conf.get("adhoc")),
        isolation=bool(sub_conf.get("worktree")),
        background=getattr(sub_runner, "start_background", None) is not None,
    )


def _mem_agent_id(model_config: ModelConfig | None, model: str) -> str:
    """Chave do escopo "por modelo" (agent_id): o id do preset quando há um; senão
    o modelo base prefixado (`base:<model>`), estável entre chats."""
    if model_config is not None:
        return str(model_config.id)
    return f"base:{model}"


def _resolve_memory(chat: Chat, model_config: ModelConfig | None, user: User) -> tuple[dict[str, bool], str, bool]:
    """Config de memória EFETIVA do turno: chat.memory_config → capabilities.memory
    do modelo → profile.memory do usuário → padrão. Retorna (read_dict, write_scope,
    review). `enabled` False (em qualquer nível efetivo) zera leitura e escrita.
    `review` (padrão do perfil) faz novas memórias entrarem como pendentes."""
    cfg = dict(_DEFAULT_MEMORY)
    review = bool(((user.profile or {}).get("memory") or {}).get("review"))
    cfg.update((user.profile or {}).get("memory") or {})
    if model_config is not None:
        cfg.update((model_config.capabilities or {}).get("memory") or {})
    if chat.memory_config:
        cfg.update(chat.memory_config)
    if cfg.get("enabled") is False:
        return {"global": False, "model": False, "chat": False, "project": False}, "off", review
    read = {**_DEFAULT_MEMORY["read"], **(cfg.get("read") or {})}
    write = cfg.get("write") or "global"
    return read, write, review


def _mem_banks(chat: Chat, model_config: ModelConfig | None, user: User) -> list[str]:
    """Bancos de memória ACOPLADOS (ids) na config efetiva do turno — lidos em
    UNIÃO com os escopos normais. Camadas: perfil → modelo → chat. Vazio quando a
    memória está desligada no nível efetivo."""
    cfg = dict(_DEFAULT_MEMORY)
    cfg.update((user.profile or {}).get("memory") or {})
    if model_config is not None:
        cfg.update((model_config.capabilities or {}).get("memory") or {})
    if chat.memory_config:
        cfg.update(chat.memory_config)
    if cfg.get("enabled") is False:
        return []
    banks = cfg.get("banks") or []
    return [str(b) for b in banks if b]


def _resolve_knowledge(chat: Chat | None, model_config: ModelConfig | None, user: User) -> dict:
    """Config EFETIVA da Base de Conhecimento no turno. As bases acopladas são a
    UNIÃO de perfil + modelo + chat (o usuário pode acoplar por-modelo E por-chat);
    `mode`/`k` seguem a camada mais específica. `enabled: False` (mais específico)
    desliga tudo. `chat=None` (chat efêmero) usa só perfil+modelo. Retorna
    {bases, mode, k} p/ o orchestrator."""
    prof = (user.profile or {}).get("knowledge") or {}
    mc = ((model_config.capabilities or {}).get("knowledge") or {}) if model_config is not None else {}
    chat_cfg = (chat.knowledge_config or {}) if chat is not None else {}
    merged = {**prof, **mc, **chat_cfg}
    if merged.get("enabled") is False:
        return {"bases": [], "mode": "auto", "modes": {}, "ks": {}, "k": 6}
    bases: list[str] = []
    for src in (prof, mc, chat_cfg):
        for b in src.get("bases") or []:
            if b and str(b) not in bases:
                bases.append(str(b))
    # override de MODO por base (auto|tool); a camada mais específica vence
    modes: dict[str, str] = {}
    for src in (prof, mc, chat_cfg):
        for k, v in (src.get("modes") or {}).items():
            if str(v).lower() in ("auto", "tool"):
                modes[str(k)] = str(v).lower()
    # override de K (trechos por busca) por base; a camada mais específica vence
    ks: dict[str, int] = {}
    for src in (prof, mc, chat_cfg):
        for bid, v in (src.get("ks") or {}).items():
            try:
                ks[str(bid)] = max(1, min(20, int(v)))
            except (TypeError, ValueError):
                continue
    return {
        "bases": bases, "mode": (merged.get("mode") or "auto"),
        "modes": modes, "ks": ks, "k": int(merged.get("k") or 6),
    }


def _resolve_brain(chat: Chat | None, model_config: ModelConfig | None, user: User) -> dict:
    """Config EFETIVA do second brain no turno (espelho do _resolve_knowledge,
    acoplamento SEPARADO — cérebros têm semântica de ESCRITA pela IA). Os cérebros
    são a UNIÃO de perfil + modelo + chat; `write`/`k` seguem a camada mais
    específica. `enabled: False` (mais específico) desliga tudo. Retorna
    {brains, write, k} p/ o orchestrator (vazio = tool não é injetada)."""
    prof = (user.profile or {}).get("brain") or {}
    mc = ((model_config.capabilities or {}).get("brain") or {}) if model_config is not None else {}
    chat_cfg = (chat.brain_config or {}) if chat is not None else {}
    merged = {**prof, **mc, **chat_cfg}
    if merged.get("enabled") is False:
        return {"brains": [], "write": False, "k": 6}
    brains: list[str] = []
    for src in (prof, mc, chat_cfg):
        for b in src.get("brains") or []:
            if b and str(b) not in brains:
                brains.append(str(b))
    return {
        "brains": brains,
        "write": bool(merged.get("write", True)),
        "k": int(merged.get("k") or 6),
    }


async def _brain_setup(
    db: AsyncSession, user: User, chat: Chat | None, model_config: ModelConfig | None,
) -> dict | None:
    """Resolve o cérebro do turno E valida os ids no banco (posse + kind="brain" —
    ids arbitrários em brain_config não podem apontar p/ base de outro usuário nem
    p/ uma base RAG comum). Anexa os NOMES (p/ o system e a escolha de destino no
    write). None = tool `brain` não é injetada."""
    cfg = _resolve_brain(chat, model_config, user)
    raw = cfg.get("brains") or []
    if not raw:
        return None
    ids: list[uuid.UUID] = []
    for b in raw:
        try:
            ids.append(uuid.UUID(str(b)))
        except (ValueError, TypeError):
            continue
    if not ids:
        return None
    rows = list(await db.scalars(
        select(KnowledgeBase).where(
            KnowledgeBase.id.in_(ids),
            KnowledgeBase.user_id == user.id,
            KnowledgeBase.kind == "brain",
        )
    ))
    if not rows:
        return None
    return {
        "brains": [str(b.id) for b in rows],
        "names": [b.name or "Cérebro" for b in rows],
        "write": bool(cfg.get("write")),
        "k": int(cfg.get("k") or 6),
    }


def _has_capability(model_config: ModelConfig | None, key: str, *, default: bool) -> bool:
    """Lê uma capacidade booleana do modelo; `default` quando ausente/inválida."""
    if model_config is None:
        return default
    v = (model_config.capabilities or {}).get(key)
    return bool(v) if isinstance(v, bool) else default


def _realtime_datetime(model_config: ModelConfig | None) -> bool:
    """Capacidade "Data e Hora em Tempo Real": injeta a data/hora atual (fuso do
    usuário) no system a cada turno. Padrão LIGADO (ausente = ligada) — desligue no
    modelo p/ economizar tokens quando ele não precisa saber "agora" (ex.: roleplay)."""
    return _has_capability(model_config, "realtime_datetime", default=True)


def _skill_learning(model_config: ModelConfig | None) -> bool | None:
    """Capacidade "Aprender skills" (/learn): o modelo ganha a tool `propose_skill`
    (proposta-com-aprovação — NUNCA grava sozinha). Tri-state: True força, False
    desliga, None = AUTO (o orchestrator injeta só se o turno já anuncia outras
    tools — modelos sem tool-calling não recebem `tools` no request)."""
    if model_config is None:
        return None
    v = (model_config.capabilities or {}).get("skill_learning")
    return v if isinstance(v, bool) else None


async def _ref_docs(
    db: AsyncSession, user: User, chat: Chat | None,
    model_config: ModelConfig | None, ids: list[uuid.UUID],
) -> list[dict]:
    """Docs da Base de Conhecimento referenciados no compositor ("#") p/ ESTE turno.
    GATE: só entram docs de bases ACOPLADAS ao modelo/chat (mesma resolução do RAG).
    Devolve [{id, filename, base_id, text}] com o texto extraído (p/ o orchestrator
    decidir texto-inteiro vs. trechos)."""
    if not ids:
        return []
    from ..knowledge import ingest as kb_ingest

    allowed = {str(b) for b in _resolve_knowledge(chat, model_config, user).get("bases") or []}
    if not allowed:
        return []
    out: list[dict] = []
    for did in ids:
        d = await db.get(KnowledgeDoc, did)
        if d is None or d.user_id != user.id or str(d.base_id) not in allowed:
            continue
        if not d.data:
            continue
        try:
            text = await run_in_threadpool(kb_ingest.extract_text, d.filename, d.mime, bytes(d.data))
        except Exception:  # noqa: BLE001
            continue
        if text and text.strip():
            out.append({
                "id": str(d.id), "filename": d.filename,
                "base_id": str(d.base_id), "text": text,
            })
    return out


async def _ref_chats(
    db: AsyncSession, user: User, ids: list[uuid.UUID],
) -> list[dict]:
    """Chats de Referência anexados no compositor p/ ESTE turno. Devolve
    [{id, title, transcript}] com uma transcrição RESUMIDA (últimas mensagens, sem
    as compactadas/resumos) de cada chat DO PRÓPRIO usuário. Bounded p/ não estourar
    o contexto: teto de mensagens por chat e de chars por transcrição."""
    if not ids:
        return []
    per_chat_msgs = 40      # últimas N mensagens de cada chat
    per_chat_chars = 6000   # teto de chars da transcrição de cada chat
    out: list[dict] = []
    for cid in ids[:5]:
        chat = await db.get(Chat, cid)
        if chat is None or chat.user_id != user.id:
            continue
        msgs = await _ordered_messages(db, cid)
        # ignora resumos de compactação e mensagens fora de contexto
        live = [m for m in msgs if not getattr(m, "is_summary", False) and not getattr(m, "compacted", False)]
        lines: list[str] = []
        for m in live[-per_chat_msgs:]:
            text = (m.content or "").strip()
            if not text:
                continue
            who = "Usuário" if m.role == "user" else "Assistente"
            lines.append(f"{who}: {text}")
        transcript = "\n".join(lines).strip()
        if not transcript:
            continue
        if len(transcript) > per_chat_chars:  # mantém o FIM (mais recente/relevante)
            transcript = "…\n" + transcript[-per_chat_chars:]
        out.append({"id": str(chat.id), "title": chat.title or "Chat", "transcript": transcript})
    return out


def _image_output(model_config: ModelConfig | None) -> bool:
    """Capacidade "Geração de Imagens": o modelo gera imagens NATIVAMENTE (inline,
    ex.: nano banana / gemini-2.5-flash-image) — via modalities=["image","text"].
    Distinta do filtro GenImage Router (que delega a OUTRO modelo)."""
    return bool(model_config and (model_config.capabilities or {}).get("image_generation"))


def _ocr_prefs(model_config: ModelConfig | None) -> tuple[bool, str, str]:
    """(ocr_ligado, motor, idioma) da extração — config POR-MODELO."""
    te = tool_config(model_config).get("text_extraction") or {}
    ocr = te.get("ocr", True) is not False
    engine = te.get("ocr_engine") or "tesseract"
    lang = te.get("ocr_lang") or "por+eng"
    return ocr, engine, lang


def _vision_router_model(model_config: ModelConfig | None) -> str | None:
    """Modelo alvo do filtro Vision Router (descreve imagens p/ modelo sem visão)."""
    if model_config is None:
        return None
    caps = model_config.capabilities or {}
    if not caps.get("filter:vision_router"):
        return None
    cfg = (model_config.filter_config or {}).get("vision_router") or {}
    return (cfg.get("model") or "").strip() or None


async def _audio_router_config(
    db: AsyncSession, user: User, model_config: ModelConfig | None
) -> dict | None:
    """Config do filtro Audio Router (transcreve áudios anexados p/ o modelo
    'ouvir'). Espelha o Vision Router; resolve as credenciais AQUI para o dict ser
    autossuficiente no orchestrator (que não tem db). Motores:
      - "stt" (padrão): provedor de voz global (Whisper) — precisa da chave de voz.
      - "model": um modelo multimodal de áudio via OpenRouter."""
    if model_config is None:
        return None
    caps = model_config.capabilities or {}
    if not caps.get("filter:audio_router"):
        return None
    cfg = (model_config.filter_config or {}).get("audio_router") or {}
    engine = (cfg.get("engine") or "stt").strip()
    if engine == "model":
        model = (cfg.get("model") or "").strip()
        return {"engine": "model", "model": model} if model else None
    # motor "stt": monta a CADEIA — voz local do usuário → provedor global → modelo
    # multimodal via OpenRouter (a chave do turno já existe). O runtime tenta na
    # ordem; antes, sem chave de voz o canal ficava SURDO em silêncio.
    from ..integrations import voice_service
    s = get_settings()
    stt_chain: list[dict] = []
    prov = await voice_service.get_provider(db, user.id)
    if prov:
        stt_chain.append({"engine": "stt", "base_url": prov["base_url"],
                          "api_key": prov["api_key"], "model": s.stt_model})
    key = await get_secret(db, user.id, VOICE_KEY)
    if key:
        stt_chain.append({"engine": "stt", "base_url": s.voice_base_url,
                          "api_key": key, "model": s.stt_model})
    stt_chain.append({"engine": "model", "model": _AUDIO_FALLBACK_MODEL})
    head, *rest = stt_chain
    return {**head, "fallbacks": rest}


async def _genimage_config(
    db: AsyncSession, user: User, model_config: ModelConfig | None
) -> dict | None:
    """Config do filtro GenImage Router (roteia geração de imagem a um modelo de
    imagem). Espelha _vision_router_model. Resolve a chave do provedor externo aqui
    p/ o dict ser autossuficiente no driver de background."""
    if model_config is None:
        return None
    caps = model_config.capabilities or {}
    if not caps.get("filter:genimage_router"):
        return None
    cfg = (model_config.filter_config or {}).get("genimage_router") or {}
    model = (cfg.get("model") or "").strip()
    if not model:
        return None
    provider = (cfg.get("provider") or "openrouter").strip()
    out: dict = {"provider": provider, "model": model, "base_url": (cfg.get("base_url") or "").strip()}
    if provider == "openai_compat":
        out["imagegen_key"] = await get_secret(db, user.id, IMAGEGEN_KEY)
    return out


# reforço padrão quando um guarda "reinforce" não tem texto próprio — sem isto o
# retry repetiria o mesmo prompt e provavelmente a mesma resposta rejeitada.
_DEFAULT_REINFORCE = (
    "Sua resposta anterior foi barrada por um filtro de qualidade. Reavalie o pedido "
    "e produza uma resposta completa, útil e direta, sem recusas, ressalvas "
    "desnecessárias nem preâmbulos."
)

# Guarda de FUNDAMENTAÇÃO/OBJETIVO (juiz que enxerga o Ledger da tarefa): critério e
# reforço padrão usados quando o guarda tem include_ledger ligado e não traz texto próprio.
# Endereça a deriva observada no Metabase (o modelo "validou correções" quando o objetivo
# era atacar; e re-reportar achado já refutado).
_GROUNDING_CRITERION = (
    "Aja (SIM) se a RESPOSTA tiver QUALQUER um destes problemas em relação ao LEDGER DA TAREFA:\n"
    "1) DESVIO DE OBJETIVO: faz/propõe algo fora do objetivo declarado (ex.: 'corrigir/implementar' "
    "quando o objetivo é analisar/atacar/investigar; trocar de alvo sem o usuário pedir).\n"
    "2) CONCLUSÃO SEM FUNDAMENTO: afirma um achado/resultado como fato sem evidência observada "
    "(execução, resposta HTTP, teste, saída real) — especulação apresentada como comprovada.\n"
    "3) ACHADO DERRUBADO: reapresenta como válido um achado marcado 'refuted' (✗) no ledger.\n"
    "Responda NÃO se a resposta está alinhada ao objetivo e toda afirmação forte tem lastro "
    "(ou é declarada explicitamente como hipótese/pendente)."
)
_GROUNDING_REINFORCE = (
    "Sua resposta anterior desviou do OBJETIVO do ledger da tarefa ou afirmou conclusões sem "
    "evidência observada. Reancore no objetivo declarado no Ledger; NÃO faça algo fora dele; toda "
    "afirmação forte deve ter lastro em evidência real (execução/HTTP/teste) — o que for hipótese, "
    "rotule como hipótese; NÃO reapresente achados marcados refuted (✗). Refaça a resposta assim."
)


async def _resolve_guards(
    db: AsyncSession, user: User, model_config: ModelConfig | None
) -> list[dict]:
    """Guardas de saída configurados no modelo (filter_config.output_guard.guards).
    Resolve AQUI o provedor de cada guarda de fallback (chave/base_url) para o dict
    ser autossuficiente no driver de background. Ver orchestrator.run_turn_guarded."""
    if model_config is None:
        return []
    caps = model_config.capabilities or {}
    if not caps.get("filter:output_guard"):
        return []
    cfg = (model_config.filter_config or {}).get("output_guard") or {}
    out: list[dict] = []
    for g in cfg.get("guards") or []:
        if not isinstance(g, dict) or not g.get("enabled", True):
            continue
        action = g.get("action") or "reinforce"
        detect = g.get("detect") or "refusal"
        guard = {
            "id": str(g.get("id") or uuid.uuid4()),
            "name": (str(g.get("name") or "Guarda de saída").strip() or "Guarda de saída")[:120],
            "enabled": True,
            "detect": detect,
            "pattern": str(g.get("pattern") or ""),
            "min_len": int(g.get("min_len") or 0),
            "judge_model": str(g.get("judge_model") or "").strip(),
            "criterion": str(g.get("criterion") or ""),
            # juiz enxerga o Ledger da tarefa (objetivo/plano/achados) → detecta deriva de
            # objetivo e conclusão sem fundamento. Só faz sentido com detect=judge.
            "include_ledger": bool(g.get("include_ledger")),
            "action": action,
            "inject_text": str(g.get("inject_text") or ""),
            "fallback_model": str(g.get("fallback_model") or "").strip(),
            "max_retries": max(1, min(int(g.get("max_retries") or 1), 3)),
        }
        # detecção por juiz LLM: resolve o provedor do modelo-juiz aqui
        if detect == "judge":
            # include_ledger sem critério próprio → usa o rubric de fundamentação/objetivo
            if guard["include_ledger"] and not guard["criterion"].strip():
                guard["criterion"] = _GROUNDING_CRITERION
            if not guard["judge_model"] or not guard["criterion"].strip():
                continue  # juiz sem modelo ou sem critério → inválido, ignora
            try:
                jk, jb = await _resolve_provider(db, user, guard["judge_model"])
            except HTTPException:
                continue  # provedor do juiz indisponível
            guard["_judge_api_key"] = jk
            guard["_judge_base_url"] = jb
        if action == "fallback_model":
            if not guard["fallback_model"]:
                continue  # fallback sem modelo → guarda inútil, ignora
            try:
                key, base = await _resolve_provider(db, user, guard["fallback_model"])
            except HTTPException:
                continue  # provedor do fallback indisponível (ex.: Ollama off)
            guard["_api_key"] = key
            guard["_base_url"] = base
        elif not guard["inject_text"].strip():
            # reinforce sem texto próprio → usa o reforço padrão (senão o retry
            # repetiria o mesmo prompt e a mesma resposta). Guarda de fundamentação
            # ganha um reforço específico (reancorar no objetivo/evidência).
            guard["inject_text"] = (
                _GROUNDING_REINFORCE if guard["include_ledger"] else _DEFAULT_REINFORCE
            )
        out.append(guard)
    return out


# --------------------------------------------------------------------------- #
# Subagentes: um modelo (orquestrador) pode delegar sub-tarefas a outros
# ModelConfigs (operários). Cada operário roda um turno próprio (system/tools/skills
# dele) e devolve o resultado. Guardrails: profundidade, teto de chamadas, ciclos.
# --------------------------------------------------------------------------- #
async def _resolve_subagents(
    db: AsyncSession, user: User, model_config: ModelConfig | None
) -> tuple[list[dict], dict]:
    """(time visível ao modelo, config). Vazio se a permissão estiver desligada."""
    if model_config is None:
        return [], {}
    caps = model_config.capabilities or {}
    if not caps.get("subagents"):
        return [], {}
    cfg = (model_config.filter_config or {}).get("subagents") or {}
    uids: list[uuid.UUID] = []
    for x in cfg.get("team") or []:
        try:
            uids.append(uuid.UUID(str(x)))
        except (ValueError, TypeError):
            continue
    specs: list[dict] = []
    if uids:
        rows = await db.scalars(
            select(ModelConfig).where(
                ModelConfig.user_id == user.id,
                ModelConfig.id.in_(uids),
                ModelConfig.enabled.is_(True),
            )
        )
        by_id = {str(mc.id): mc for mc in rows}
        # preserva a ordem escolhida pelo usuário
        for x in cfg.get("team") or []:
            mc = by_id.get(str(x))
            if mc is not None:
                specs.append({"key": str(mc.id), "name": mc.name, "description": (mc.description or "")[:200]})
    # Isolamento POR-OPERÁRIO (opt-in): `isolate` é a lista de keys que trabalham num
    # worktree próprio (branch isolada) — um revisor "só leitura" fica de fora e não abre
    # tarefa à toa. Legado: o bool global `worktree_isolation` liga TODOS (compat com
    # configs antigas; a UI nova escreve `isolate`).
    team_keys = [s["key"] for s in specs]
    iso_raw = cfg.get("isolate")
    if isinstance(iso_raw, list):
        isolate = [str(x) for x in iso_raw if str(x) in set(team_keys)]
    elif cfg.get("worktree_isolation"):
        isolate = list(team_keys)
    else:
        isolate = []
    # `worktree` é o interruptor geral (sem ele ninguém isola). Config antiga, sem a
    # chave: ligado se já havia algum operário isolado.
    worktree = cfg.get("worktree")
    if not isinstance(worktree, bool):
        worktree = bool(isolate)
    if not worktree:
        isolate = []
    conf = {
        # padrão paralelo. `execution` é a escolha explícita; o `mode` antigo é ignorado
        # porque o editor antigo gravava "sequential" em todo modelo, escolhido ou não
        "mode": "sequential" if cfg.get("execution") == "sequential" else "parallel",
        # teto de agentes do TURNO (a árvore toda, sub-equipes incluídas) e quantos
        # rodam ao mesmo tempo; o resto espera na fila
        "max_calls": max(1, min(int(cfg.get("max_calls") or 4), MAX_AGENTS)),
        "concurrency": max(1, min(int(cfg.get("concurrency") or 8), MAX_CONCURRENCY)),
        "max_depth": max(1, min(int(cfg.get("max_depth") or 2), 3)),
        # opt-in: operários enxergam o histórico do chat / usam a própria memória
        "pass_context": bool(cfg.get("pass_context")),
        "worker_memory": bool(cfg.get("worker_memory")),
        "isolate": isolate,
        "worktree": worktree,
        # a IA pode criar agentes para a tarefa (agent="new"); padrão: sim
        "adhoc": cfg.get("adhoc") is not False,
        # modelo dos agentes criados pela IA; vazio = o mesmo do orquestrador
        "adhoc_model": str(cfg.get("adhoc_model") or "").strip()[:255],
    }
    return specs, conf


async def subagents_for_turn(
    db: AsyncSession, user: User, chat_id: uuid.UUID | None, project_id: str | None,
    model_config: ModelConfig | None,
) -> SubagentOpts | None:
    """Delegação do turno (tool `delegate`), ou None se o modelo não delega."""
    specs, conf = await _resolve_subagents(db, user, model_config)
    if not specs and not conf.get("adhoc"):
        return None
    runner = _make_subagent_runner(
        db, user, chat_id, conf.get("max_depth", 2),
        pass_context=conf.get("pass_context", False),
        worker_memory=conf.get("worker_memory", False),
        project_id=project_id,
        isolate_keys=frozenset(conf.get("isolate") or []),
        parent=model_config, adhoc_model=conf.get("adhoc_model", ""),
        # sequencial = um agente por vez, inclusive dentro de uma equipe
        pool=SubagentPool(conf.get("max_calls", 4),
                          conf.get("concurrency", 8) if conf.get("mode") != "sequential" else 1, user.id),
    )
    return _subagent_opts(specs, {**conf, "worktree": conf.get("worktree") and bool(project_id)}, runner)


async def _recent_history(db: AsyncSession, chat_id: uuid.UUID, limit: int = 20) -> list[dict]:
    """Últimas mensagens (não-compactadas) do chat, no formato do modelo — p/ dar
    contexto da conversa a um operário quando a opção estiver ligada."""
    rows = list(await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    ))
    return (await attachment_context.history(rows))[-limit:]


_DISCOVERY_TOOLS = {"search_tools", "get_tool_schema"}


_STEP_KEYS = ("action", "path", "query", "url", "command", "cmd", "title", "symbol", "target",
              "topic", "prompt", "task", "team_name", "name")


def _step_args(name: str, args: Any) -> dict[str, Any]:
    """Os poucos argumentos que dizem O QUE o passo faz (a UI monta a frase: "Editando
    src/app.ts", "Pesquisando “x”"), curtos; nunca o conteúdo de um arquivo."""
    a = args if isinstance(args, dict) else {}
    if name == "execute_tool":
        a = a.get("params") if isinstance(a.get("params"), dict) else {}
    out: dict[str, Any] = {}
    for k in _STEP_KEYS:
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = " ".join(v.split())[:120]
    diff = a.get("diff")
    if isinstance(diff, str) and diff:
        files = [ln[4:].strip().removeprefix("b/") for ln in diff.splitlines() if ln.startswith("+++ ")]
        files = [f for f in files if f and f != "/dev/null"]
        if files:
            out["files"] = files[:20]
    members = a.get("members")
    if isinstance(members, list):
        out["members"] = len(members)
    return out


def _step_of(name: str, args: Any) -> tuple[str, str] | None:
    """(ferramenta legível, detalhe curto) de uma chamada do subagente; None = ruído
    de descoberta, que não vira passo."""
    if name in _DISCOVERY_TOOLS:
        return None
    a = args if isinstance(args, dict) else {}
    if name == "execute_tool":
        name, a = str(a.get("path") or "execute_tool"), a.get("params") or {}
    detalhe = next((v for v in (a.values() if isinstance(a, dict) else []) if isinstance(v, str) and v.strip()), "")
    return name.replace("__", "."), " ".join(detalhe.split())[:120]


_ADHOC_PREAMBLE = (
    "You are {name}, a subagent the main assistant created for one task. Work on it "
    "autonomously with your tools. Your final message goes back to the main assistant, "
    "not to the user: make it a complete, concise report of what you found or did."
)

_SYNTH_PROMPT = (
    "You merge reports written by a team of agents into one report for the lead. Keep every "
    "concrete finding, number, file, decision and disagreement; drop repetition and filler. "
    "Group by theme, say which agents support each point, and flag gaps or conflicts. "
    "Write in the language of the reports."
)


def _make_subagent_runner(
    db: AsyncSession, user: User, chat_id: uuid.UUID | None, max_depth: int,
    pass_context: bool = False, worker_memory: bool = False,
    depth: int = 0, ancestry: frozenset[str] = frozenset(),
    project_id: str | None = None, isolate_keys: frozenset[str] = frozenset(),
    parent: ModelConfig | None = None, adhoc_model: str = "",
    pool: SubagentPool | None = None,
):
    """Closure que executa um subagente num turno aninhado. Dois tipos:
      - agente do usuário (`key` = id de um ModelConfig): prompt/tools/skills dele;
        pode delegar de novo enquanto houver profundidade (flags da config DELE);
      - agente criado pela IA (`new` = {name, instructions, isolated}): roda com as
        tools/skills do orquestrador (`parent`), no `adhoc_model` ou no modelo dele,
        sem memória; enquanto houver profundidade, pode montar a própria equipe.
    Todos dividem o `pool` do turno: teto de agentes, fila de concorrência (por nível),
    orçamento e o lock da sessão de banco (os agentes rodam concorrentes).
    Opções: `pass_context` (histórico do chat), `worker_memory` (memória própria, só
    agentes do usuário) e worktree isolado — `isolate_keys` para os do usuário,
    `new["isolated"]` para os criados. Blinda contra ciclos e recursão profunda."""
    pool = pool or SubagentPool(4, 8, user.id)

    async def _db(fn):
        """Uso da sessão do turno: um agente por vez (AsyncSession não é concorrente)."""
        async with pool.db_lock:
            return await fn()

    async def _execute(*, label: str, model: str, api_key: str, base_url: str, task: str,
                       system: str, params: dict, sift: Any, skills: list, code_mode: bool,
                       agent_id: str, memory: MemoryOpts, sub_opts: SubagentOpts | None,
                       isolated: bool, usage_mc: ModelConfig | None,
                       progress: Callable[[dict], None] | None = None,
                       with_history: bool = True) -> dict:
        history: list[dict] = []
        if with_history and pass_context and chat_id:
            if "history" not in pool.cache:
                pool.cache["history"] = await _db(lambda: _recent_history(db, chat_id))
            history = pool.cache["history"]
        # worktree isolado: branch própria do projeto do chat; o resultado vira uma
        # tarefa `awaiting_review` que o humano aprova/descarta na UI
        wt_task_id: str | None = None
        if isolated and project_id:
            from ..codespace import worktree_service
            opened = await worktree_service.open_task(
                str(user.id), project_id, title=task[:200], agent=label,
                chat_id=str(chat_id) if chat_id else None,
            )
            if opened.get("error"):
                return {"error": f"não consegui abrir o worktree isolado: {opened['error']}"}
            wt_task_id = opened.get("id")
        collected = ""
        usage = None
        # passos do subagente (ferramenta + detalhe + deu certo): vão só para a UI
        steps: list[dict[str, Any]] = []
        abertos: dict[str, list[dict[str, Any]]] = {}
        # linha do tempo do agente (raciocínio, texto e passos, na ordem): a UI a
        # desenha como o bloco de raciocínio. O texto final é o relatório (`output`).
        timeline: list[dict[str, Any]] = []
        chars = {"reasoning": 0}
        # texto/raciocínio vão à UI AGRUPADOS (~3 por segundo), não um evento por token:
        # com dezenas de agentes em paralelo o stream não afoga
        pend: dict[str, Any] = {"kind": None, "text": "", "at": 0.0}

        def _flush_pend() -> None:
            if pend["text"] and progress is not None:
                progress({pend["kind"]: pend["text"]})
            pend["text"] = ""
            pend["at"] = time.monotonic()

        def _trilha(kind: str, text: str) -> None:
            if not text:
                return
            if kind == "reasoning":
                if chars["reasoning"] >= 20000:
                    return
                chars["reasoning"] += len(text)
            if timeline and timeline[-1]["kind"] == kind:
                timeline[-1]["text"] += text
            else:
                timeline.append({"kind": kind, "text": text})
            if pend["kind"] != kind:
                _flush_pend()
                pend["kind"] = kind
            pend["text"] += text
            if time.monotonic() - pend["at"] >= 0.3:
                _flush_pend()
        try:
            async for ev in run_turn(
                api_key=api_key, model=model, history=history, user_text=task,
                chat_system_prompt=system, params=params,
                base_url=base_url, sift=sift, code_mode=code_mode,
                session=TurnSession(
                    user_id=str(user.id),
                    chat_id=str(chat_id) if chat_id else None,
                    agent_id=agent_id,
                    codespace_project_id=project_id if wt_task_id else None,
                    codespace_worktree=wt_task_id,
                ),
                memory=memory, skills=skills, use_context=True, subagent=sub_opts,
            ):
                t = ev.get("type")
                if t == "token":
                    collected += ev.get("text", "")
                    _trilha("text", ev.get("text", ""))
                elif t == "reasoning":
                    _trilha("reasoning", ev.get("text", ""))
                elif t == "done":
                    collected = ev.get("content") or collected
                    usage = ev.get("usage")
                elif t == "tool_call":
                    _flush_pend()
                    passo = _step_of(str(ev.get("name") or ""), ev.get("arguments"))
                    if passo is not None and len(steps) < 60:
                        item = {"tool": passo[0], "detail": passo[1], "ok": None}
                        resumo = _step_args(str(ev.get("name") or ""), ev.get("arguments"))
                        steps.append(item)
                        timeline.append({"kind": "tool", **item, "args": resumo})
                        abertos.setdefault(str(ev.get("name")), []).append(item)
                        if progress is not None:
                            progress({"tool": passo[0], "detail": passo[1], "args": resumo})
                elif t == "tool_result":
                    fila = abertos.get(str(ev.get("name")))
                    if fila:
                        res = ev.get("result")
                        feito = fila.pop(0)
                        feito["ok"] = not (isinstance(res, dict) and res.get("error"))
                        for passo_t in reversed(timeline):
                            if passo_t["kind"] == "tool" and passo_t["tool"] == feito["tool"] and passo_t["ok"] is None:
                                passo_t["ok"] = feito["ok"]
                                break
                        if progress is not None:
                            progress({"result": feito["tool"], "ok": feito["ok"]})
        except Exception as exc:  # noqa: BLE001
            if wt_task_id:
                from ..codespace import worktree_service
                try:
                    await worktree_service.mark_awaiting(wt_task_id)
                except Exception:  # noqa: BLE001
                    pass
            return {"error": f"o subagente falhou: {exc}"}
        # fecha o worktree como pronto p/ revisão (commita o resto + calcula o diff)
        if wt_task_id:
            from ..codespace import worktree_service
            try:
                await worktree_service.mark_awaiting(wt_task_id)
            except Exception:  # noqa: BLE001
                pass
        # analítica por-agente: registra o uso do subagente no ledger
        if usage:
            try:
                rec = _usage_record(usage, model, usage_mc)
                async with SessionLocal() as s:
                    uev = usage_event_from_record(user.id, chat_id, uuid.uuid4(), rec)
                    if uev is not None:
                        s.add(uev)
                        await s.commit()
            except Exception:  # noqa: BLE001 - ledger é best-effort
                pass
        _flush_pend()
        # o texto que fecha a linha do tempo é o próprio relatório: não duplica
        if timeline and timeline[-1]["kind"] == "text":
            timeline.pop()
        out = {"kind": "subagent", "agent": label, "task": task,
               "output": collected or "(sem resposta)", "steps": steps, "timeline": timeline}
        if wt_task_id:
            out["task_id"] = wt_task_id
            out["note"] = ("O trabalho ficou num worktree isolado (tarefa a revisar) — "
                           "NÃO foi mesclado ainda; o usuário aprova/descarta na aba Tarefas.")
        return out

    async def _adhoc_prep() -> dict | str:
        """Provedor, tools e skills dos agentes criados pela IA: iguais para todos eles,
        resolvidos UMA vez por turno (mil agentes não viram mil consultas)."""
        if "adhoc" in pool.cache:
            return pool.cache["adhoc"]
        if parent is None or not parent.base_model:
            return "agentes criados pela IA precisam de um modelo custom como orquestrador"
        model = adhoc_model or parent.base_model
        try:
            api_key, base_url = await _db(lambda: _resolve_provider(db, user, model))
        except HTTPException as exc:
            return f"provedor do subagente indisponível: {exc.detail}"
        prep = {
            "model": model, "api_key": api_key, "base_url": base_url,
            "sift": await _db(lambda: get_sift_for_user(db, user.id, parent)),
            "skills": await _db(lambda: _load_skills(db, user, parent)),
        }
        pool.cache["adhoc"] = prep
        return prep

    def _team_opts() -> SubagentOpts | None:
        """Equipe própria de um agente criado pela IA (líder), se ainda houver profundidade."""
        if depth + 1 >= max_depth:
            return None
        nested = _make_subagent_runner(
            db, user, chat_id, max_depth, pass_context=pass_context, worker_memory=False,
            depth=depth + 1, ancestry=ancestry, project_id=project_id,
            parent=parent, adhoc_model=adhoc_model, pool=pool,
        )
        return SubagentOpts(agents=[], run=nested, mode="parallel", max_calls=pool.limit,
                            adhoc=True, isolation=bool(project_id), background=False)

    async def _run_new(new: dict, task: str, progress: Callable[[dict], None] | None) -> dict:
        prep = await _adhoc_prep()
        if isinstance(prep, str):
            return {"error": prep}
        name = str(new.get("name") or "Agente")
        out = await _execute(
            label=name, model=prep["model"], api_key=prep["api_key"], base_url=prep["base_url"], task=task,
            system=_ADHOC_PREAMBLE.format(name=name) + "\n\n" + str(new.get("instructions") or ""),
            params=parent.params or {}, sift=prep["sift"],
            skills=prep["skills"], code_mode=_code_mode(parent),
            agent_id=_mem_agent_id(parent, prep["model"]), memory=MemoryOpts(read=None, write="off"),
            sub_opts=_team_opts(), isolated=bool(new.get("isolated")), usage_mc=parent, progress=progress,
        )
        if "error" not in out:
            out["adhoc"] = True
        return out

    async def synthesize(goal: str, reports: str) -> str:
        """Um agente de síntese (sem ferramentas) junta um lote de relatórios."""
        prep = await _adhoc_prep()
        if isinstance(prep, str):
            raise RuntimeError(prep)
        async with pool.slot(depth):
            out = await _execute(
                label="Síntese", model=prep["model"], api_key=prep["api_key"], base_url=prep["base_url"],
                task=f"Overall goal: {goal}\n\nReports:\n\n{reports}",
                system=_SYNTH_PROMPT, params=(parent.params if parent else None) or {}, sift=None,
                skills=[], code_mode=False, agent_id=_mem_agent_id(parent, prep["model"]),
                memory=MemoryOpts(read=None, write="off"), sub_opts=None, isolated=False,
                usage_mc=parent, with_history=False,
            )
        if out.get("error"):
            raise RuntimeError(out["error"])
        return str(out.get("output") or "")

    async def run_subagent(key: str, task: str, new: dict | None = None,
                           progress: Callable[[dict], None] | None = None) -> dict:
        if not pool.take():
            return {"error": f"limite de {pool.limit} agentes deste turno atingido"}
        # fila: no máximo `concurrency` agentes deste nível trabalhando ao mesmo tempo. A
        # vaga vem ANTES de qualquer await, para a fila seguir a ordem da equipe
        async with pool.slot(depth):
            if await pool.budget_blocked():
                return {"error": "orçamento mensal atingido (modo pausar): agente não iniciado"}
            if progress is not None:
                progress({"state": "running"})
            return await _run_subagent(key, task, new, progress)

    async def _run_subagent(key: str, task: str, new: dict | None,
                            progress: Callable[[dict], None] | None) -> dict:
        if new is not None:
            return await _run_new(new, task, progress)
        if key in ancestry:
            return {"error": "ciclo de subagentes detectado; delegação abortada"}
        try:
            wid = uuid.UUID(str(key))
        except (ValueError, TypeError):
            return {"error": "subagente inválido"}
        mc = await _db(lambda: db.get(ModelConfig, wid))
        if mc is None or mc.user_id != user.id or not mc.enabled or not mc.base_model:
            return {"error": "subagente não encontrado ou desabilitado"}
        try:
            api_key, base_url = await _db(lambda: _resolve_provider(db, user, mc.base_model))
        except HTTPException as exc:
            return {"error": f"provedor do subagente indisponível: {exc.detail}"}
        # delegação em cadeia: só se ainda houver profundidade. Os flags de contexto/
        # memória do operário-de-2º-nível vêm da config DELE (ele vira o orquestrador).
        sub_opts: SubagentOpts | None = None
        if depth + 1 < max_depth:
            sub_specs, sub_conf = await _db(lambda: _resolve_subagents(db, user, mc))
            if sub_specs or sub_conf.get("adhoc"):
                nested_runner = _make_subagent_runner(
                    db, user, chat_id, max_depth,
                    pass_context=sub_conf.get("pass_context", False),
                    worker_memory=sub_conf.get("worker_memory", False),
                    depth=depth + 1, ancestry=ancestry | {key},
                    project_id=project_id,
                    isolate_keys=frozenset(sub_conf.get("isolate") or []),
                    parent=mc, adhoc_model=sub_conf.get("adhoc_model", ""), pool=pool,
                )
                sub_opts = _subagent_opts(
                    sub_specs, {**sub_conf, "worktree": sub_conf.get("worktree") and bool(project_id)},
                    nested_runner)
        # memória própria do operário (opt-in): usa a config do modelo do operário
        mem_read: dict | None = None
        mem_write = "off"
        mem_review = False
        if worker_memory and chat_id:
            _stub = SimpleNamespace(memory_config=None)
            mem_read, mem_write, mem_review = _resolve_memory(_stub, mc, user)  # type: ignore[arg-type]
        return await _execute(
            label=mc.name, model=mc.base_model, api_key=api_key, base_url=base_url, task=task,
            system=mc.system_prompt, params=mc.params or {},
            sift=await _db(lambda: get_sift_for_user(db, user.id, mc)),
            skills=await _db(lambda: _load_skills(db, user, mc)),
            code_mode=_code_mode(mc), agent_id=_mem_agent_id(mc, mc.base_model),
            memory=MemoryOpts(read=mem_read, write=mem_write, review=mem_review),
            sub_opts=sub_opts, isolated=key in isolate_keys, usage_mc=mc, progress=progress,
        )

    def start_background(key: str, task: str, new: dict | None, label: str) -> str:
        """Solta o subagente em segundo plano; o chat é acordado com o relatório. Roda
        numa sessão de banco PRÓPRIA: a do turno fecha quando o turno acaba."""
        from . import subagent_jobs

        user_id, parent_id = user.id, (parent.id if parent is not None else None)

        async def _job() -> dict:
            async with SessionLocal() as s:
                u = await s.get(User, user_id)
                p = await s.get(ModelConfig, parent_id) if parent_id else None
                if u is None:
                    return {"error": "usuário não encontrado"}
                runner = _make_subagent_runner(
                    s, u, chat_id, max_depth, pass_context=pass_context,
                    worker_memory=worker_memory, depth=depth, ancestry=ancestry,
                    project_id=project_id, isolate_keys=isolate_keys,
                    parent=p, adhoc_model=adhoc_model, pool=bg_pool,
                )
                return await runner(key, task, new)

        bg_pool = pool.fork()
        return subagent_jobs.start(str(chat_id), label, task, _job)

    def start_background_team(members: list[dict], goal: str, label: str, chain: bool = False) -> str:
        """Solta a equipe inteira em segundo plano; o chat acorda com o relatório final."""
        from . import subagent_jobs
        from .subagent_team import run_team

        user_id, parent_id = user.id, (parent.id if parent is not None else None)
        bg_pool = pool.fork()

        async def _job() -> dict:
            async with SessionLocal() as s:
                u = await s.get(User, user_id)
                p = await s.get(ModelConfig, parent_id) if parent_id else None
                if u is None:
                    return {"error": "usuário não encontrado"}
                runner = _make_subagent_runner(
                    s, u, chat_id, max_depth, pass_context=pass_context,
                    worker_memory=worker_memory, depth=depth, ancestry=ancestry,
                    project_id=project_id, isolate_keys=isolate_keys,
                    parent=p, adhoc_model=adhoc_model, pool=bg_pool,
                )
                res = await run_team(runner, members, goal, lambda ev: None, runner.synthesize,
                                     chain=chain)
                return {"output": res["report"],
                        "note": f"{res['succeeded']} of {res['size']} agents succeeded."}

        return subagent_jobs.start(str(chat_id), label, goal, _job)

    # segundo plano só com um chat para acordar
    run_subagent.start_background = start_background if chat_id else None  # type: ignore[attr-defined]
    run_subagent.start_background_team = start_background_team if chat_id else None  # type: ignore[attr-defined]
    run_subagent.synthesize = synthesize  # type: ignore[attr-defined]
    run_subagent.pool = pool  # type: ignore[attr-defined]
    return run_subagent


# teto de tamanho total dos anexos por turno (defesa; o schema já limita por item)
# Teto do que vai EMBUTIDO na requisição ao provedor (imagens em base64) — não é o
# teto do arquivo: um documento de 500MB vira texto e nunca passa por aqui.
_MAX_ATTACH_TOTAL = 20 * 1024 * 1024
# teto de QUANTIDADE de anexos por turno — bate com o schema SendMessageIn (max_length)
# e com `upload_max_per_message` (config/front).
_MAX_ATTACH_COUNT = 50


async def _resolve_upload(a: dict, ex_cfg: dict) -> dict | None:
    """Anexo por REFERÊNCIA: lê o arquivo do disco só no que o modelo precisa.

    Imagem/áudio viajam inteiros na requisição ao provedor, então respeitam o teto
    DELES (uma imagem de 300MB não existe para o modelo). Documento já chegou aqui
    com o texto extraído no upload — o binário nunca entra no contexto."""
    import uuid as _uuid

    from .. import uploads_service
    from ..db import SessionLocal
    from ..extraction import extract, is_extractable
    from ..models import Upload

    try:
        upload_id = _uuid.UUID(str(a.get("upload_id")))
    except (ValueError, TypeError):
        return None
    async with SessionLocal() as db:
        row = await db.get(Upload, upload_id)
        if row is None:
            return {"type": "file", "name": str(a.get("name") or "")[:255],
                    "text": "[anexo não está mais disponível no servidor]"}
        name = row.filename or str(a.get("name") or "")
        if row.kind in ("image", "audio"):
            limite = uploads_service.max_bytes_for(row.kind)
            data = await run_in_threadpool(uploads_service.read_bytes, row, limite)
            if data is None:
                return {"type": "file", "name": name,
                        "text": f"[{name}: grande demais para ser enviado ao modelo]"}
            mime = row.mime or ("image/png" if row.kind == "image" else "audio/mpeg")
            return {
                "type": row.kind, "name": name, "upload_id": str(row.id),
                "url": f"data:{mime};base64,{base64.b64encode(data).decode()}",
            }
        text = row.text
        if not text and is_extractable(name, row.mime):
            # o upload pode ter sido feito antes de a extração existir/funcionar
            data = await run_in_threadpool(uploads_service.read_bytes, row)
            if data is not None:
                try:
                    text = await run_in_threadpool(extract, name, row.mime, data, ex_cfg)
                except extraction.ExtractionError as exc:
                    text = f"[não foi possível extrair '{name}': {exc}]"
        if not text:
            return {
                "type": "file", "name": name, "upload_id": str(row.id),
                # a nota é lida pelo MODELO: sem dizer o que fazer, ele inventa que não
                # tem ferramenta para ler arquivos e devolve isso ao usuário
                "text": (
                    f"[O anexo '{name}' chegou ao servidor, mas não foi possível ler texto dele "
                    "(formato não suportado ou arquivo sem texto). Diga isso ao usuário e peça "
                    "o conteúdo colado ou em outro formato — não há ferramenta para abri-lo.]"
                ),
            }
        # o limite do modelo (text_extraction.max_chars) vale aqui, no envio; o texto
        # inteiro fica guardado e o modelo lê o resto com read_attachment
        limite = int(ex_cfg.get("max_chars") or extraction.DEFAULTS["max_chars"])
        limite = min(limite, 200_000) if limite > 0 else 200_000
        out = {"type": "file", "name": name, "upload_id": str(row.id), "text": text}
        if len(text) > limite:
            out["text"] = text[:limite] + (
                f"\n\n[… o arquivo continua: {len(text):,} caracteres no total, mostrados os "
                f"primeiros {limite:,}. Use read_attachment(name=\"{name}\", offset={limite}) para "
                "ler o resto, ou com `query` para buscar um trecho.]"
            ).replace(",", ".")
            out["truncated"] = True
        return out


async def _prepare_attachments(raw: Any, model_config: ModelConfig | None) -> list[dict]:
    """Prepara anexos p/ o turno.

    Dois formatos convivem: o NOVO (`upload_id` — o arquivo está no disco e a mensagem
    guarda só a referência) e o antigo (base64 embutido), que segue valendo para as
    mensagens já gravadas e para a API pública. A config de extração é POR-MODELO
    (filter_config.tools.text_extraction)."""
    if not isinstance(raw, list):
        return []
    ex_cfg = tool_config(model_config).get("text_extraction") or {}
    out: list[dict] = []
    total = 0
    for a in raw[:_MAX_ATTACH_COUNT]:
        if not isinstance(a, dict):
            continue
        if a.get("upload_id"):
            resolved = await _resolve_upload(a, ex_cfg)
            if resolved is None:
                continue
            total += len(resolved.get("url") or resolved.get("text") or "")
            if total > _MAX_ATTACH_TOTAL and resolved["type"] != "file":
                break
            out.append(resolved)
            continue
        t = a.get("type")
        if t in ("image", "audio") and isinstance(a.get("url"), str) and a["url"].startswith("data:"):
            total += len(a["url"])
            if total > _MAX_ATTACH_TOTAL:
                break
            out.append({"type": t, "name": str(a.get("name") or "")[:255], "url": a["url"]})
            continue
        if t != "file":
            continue
        name = str(a.get("name") or "")[:255]
        # arquivo de texto lido no cliente
        if isinstance(a.get("text"), str) and a["text"]:
            out.append({"type": "file", "name": name, "text": a["text"][:200_000]})
            continue
        # doc binário: extrai server-side conforme a config do usuário
        data_b64 = a.get("data")
        if isinstance(data_b64, str) and data_b64 and extraction.is_extractable(name, a.get("mime")):
            try:
                blob = base64.b64decode(data_b64, validate=False)
            except (ValueError, binascii.Error):
                out.append({"type": "file", "name": name, "text": "[anexo inválido: base64]"})
                continue
            try:
                text = await run_in_threadpool(extraction.extract, name, a.get("mime"), blob, ex_cfg)
            except extraction.ExtractionError as exc:
                out.append({"type": "file", "name": name, "text": f"[não foi possível extrair '{name}': {exc}]"})
                continue
            total += len(text)
            if total > _MAX_ATTACH_TOTAL:
                break
            out.append({"type": "file", "name": name, "text": text})
    return out


def _sse_stream(gen, *, trace_id: str | None = None):
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    # Para gerações destacadas, o trace útil é o do driver inteiro, não o trace
    # curtíssimo do POST que apenas abriu a assinatura SSE. O middleware preserva
    # este header quando já estiver presente.
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers=headers,
    )


async def _subscribe(gen: generation.Generation):
    """Repassa como SSE os eventos de uma geração em andamento (do índice 0, para
    reconstruir o parcial num assinante que chegou depois — ex.: após F5)."""
    async for event in gen.subscribe(0):
        yield _sse(event)


async def _ordered_messages(db: AsyncSession, chat_id: uuid.UUID) -> list[Message]:
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    return list(rows)


async def _imaginai_turn_kwargs(
    db: AsyncSession,
    user: User,
    chat: Chat,
    turn_key: str,
    *,
    mini_app: str | None,
) -> dict[str, Any]:
    """Ativa o World Kernel somente para um turno Imaginai explicitamente marcado.

    Uma campanha ativa é estado persistente do chat, não uma instrução global para
    toda mensagem futura. Sem esse gate, fechar o Mini App ainda deixava o protocolo
    de RPG e a ferramenta ``imaginai_world`` no contexto de conversas normais.
    """
    if mini_app != "imaginai":
        return {}
    from ..imaginai.turns import prepare_turn_tools

    tools = await prepare_turn_tools(db, user.id, chat.id, turn_key)
    return {"native_tools": tools} if tools is not None else {}
