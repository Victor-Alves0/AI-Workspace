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
import uuid
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
from ..integrations import ollama_service
from ..models import Artifact, Chat, KnowledgeBase, KnowledgeDoc, Message, ModelConfig, Skill, User
from ..secrets_service import IMAGEGEN_KEY, OPENROUTER_KEY, VOICE_KEY, get_secret
from ..tools.loader import get_sift_for_user, tool_config
from ..usage_service import usage_event_from_record
from . import artifacts as artifacts_service
from . import generation
from .orchestrator import MediaOpts, MemoryOpts, SubagentOpts, TurnSession, run_turn

logger = logging.getLogger(__name__)

# último recurso do Audio Router: modelo multimodal de áudio via OpenRouter (a
# chave do turno já existe) — mesmo exemplo que a UI do ModelEditor sugere
_AUDIO_FALLBACK_MODEL = "google/gemini-2.5-flash"


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


def _usage_record(usage: dict | None, model: str, model_config: ModelConfig | None) -> dict:
    """Monta o registro ponta-a-ponta de uma mensagem: origem + tokens + custo."""
    u = usage or {}
    rec = {
        "model": model,
        "model_config_id": str(model_config.id) if model_config else None,
        "model_name": model_config.name if model_config else model,
        # separação das FONTES no ledger: modelos locais (Ollama) ≠ API (OpenRouter)
        "provider": "ollama" if (model or "").startswith("ollama/") else "openrouter",
        "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(u.get("completion_tokens", 0) or 0),
        "total_tokens": int(u.get("total_tokens", 0) or 0),
        "reasoning_tokens": int(u.get("reasoning_tokens", 0) or 0),
        "cached_tokens": int(u.get("cached_tokens", 0) or 0),
        "cost": float(u.get("cost", 0.0) or 0.0),
    }
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
        }
        for s in rows
        if s.enabled
    ]


async def _resolve_provider(db: AsyncSession, user: User, model: str) -> tuple[str, str | None]:
    """Resolve o provedor a partir do id do modelo → (api_key, base_url).
    Modelo `ollama/*` → servidor Ollama local do usuário (OpenAI-compat, sem chave);
    senão → OpenRouter (chave do usuário). base_url=None significa OpenRouter."""
    if (model or "").startswith(ollama_service.MODEL_PREFIX):
        base = await ollama_service.get_base_url(db, user.id)
        if not base:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Ollama não configurado. Ative em Configurações → Conexões → Ollama, ou escolha outro modelo.",
            )
        return "ollama", base.rstrip("/") + "/v1"
    api_key = await get_secret(db, user.id, OPENROUTER_KEY)
    if not api_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Configure sua chave do OpenRouter primeiro"
        )
    return api_key, None


async def _prepare_turn(db: AsyncSession, user: User, chat: Chat):
    """Valida pré-requisitos e devolve (api_key, base_url, model_config, sift, skills)."""
    if not chat.model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione um modelo no chat")
    api_key, base_url = await _resolve_provider(db, user, chat.model)
    model_config = await _get_model_config(db, chat.model_config_id, user)
    sift = await get_sift_for_user(db, user.id, model_config)
    skills = await _load_skills(db, user, model_config)
    return api_key, base_url, model_config, sift, skills


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
    """Limite de aviso de uso (tokens/turno): override do modelo → padrão do perfil
    → 0 (desligado). Guarda de custo: só AVISA, não bloqueia."""
    if model_config is not None:
        v = (model_config.capabilities or {}).get("token_warn")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
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


async def _artifacts_extra(db: AsyncSession, chat_id: uuid.UUID, user: User) -> str | None:
    """Bloco de system prompt dos Artefatos: instruções de uso + conteúdo ATUAL
    dos artefatos do chat (o modelo vê edições manuais do usuário)."""
    if not _artifacts_enabled(user):
        return None
    rows = list(await db.scalars(select(Artifact).where(Artifact.chat_id == chat_id)))
    return artifacts_service.system_block(rows)


async def _artifacts_kwargs(db: AsyncSession, chat_id: uuid.UUID, user: User, arts_on: bool) -> dict:
    """kwargs de artefatos p/ o run_turn: o bloco de instruções (extra_system) + o
    rótulo "artifacts" no detalhamento de uso (painel Extenso)."""
    if not arts_on:
        return {}
    txt = await _artifacts_extra(db, chat_id, user)
    if not txt:
        return {}
    return {"extra_system": txt, "extra_breakdown": {"artifacts": len(txt)}}


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
        vision_router_model=_vision_router_model(model_config),
        audio_router=await _audio_router_config(db, user, model_config),
        ocr=ocr_on, ocr_engine=ocr_eng, ocr_lang=ocr_lang,
        genimage=await _genimage_config(db, user, model_config),
        image_output=_image_output(model_config),
    )


def _subagent_opts(sub_specs: list[dict], sub_conf: dict, sub_runner) -> SubagentOpts | None:
    if not sub_specs:
        return None
    return SubagentOpts(
        agents=sub_specs, run=sub_runner,
        mode=sub_conf.get("mode", "sequential"),
        max_calls=sub_conf.get("max_calls", 4),
        pass_context=sub_conf.get("pass_context", False),
        worker_memory=sub_conf.get("worker_memory", False),
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
        return {"bases": [], "mode": "auto", "k": 6}
    bases: list[str] = []
    for src in (prof, mc, chat_cfg):
        for b in src.get("bases") or []:
            if b and str(b) not in bases:
                bases.append(str(b))
    return {"bases": bases, "mode": (merged.get("mode") or "auto"), "k": int(merged.get("k") or 6)}


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
            "action": action,
            "inject_text": str(g.get("inject_text") or ""),
            "fallback_model": str(g.get("fallback_model") or "").strip(),
            "max_retries": max(1, min(int(g.get("max_retries") or 1), 3)),
        }
        # detecção por juiz LLM: resolve o provedor do modelo-juiz aqui
        if detect == "judge":
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
            # repetiria o mesmo prompt e a mesma resposta)
            guard["inject_text"] = _DEFAULT_REINFORCE
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
    conf = {
        "mode": "parallel" if cfg.get("mode") == "parallel" else "sequential",
        "max_calls": max(1, min(int(cfg.get("max_calls") or 4), 10)),
        "max_depth": max(1, min(int(cfg.get("max_depth") or 2), 3)),
        # opt-in: operários enxergam o histórico do chat / usam a própria memória
        "pass_context": bool(cfg.get("pass_context")),
        "worker_memory": bool(cfg.get("worker_memory")),
    }
    return specs, conf


async def _recent_history(db: AsyncSession, chat_id: uuid.UUID, limit: int = 20) -> list[dict]:
    """Últimas mensagens (não-compactadas) do chat, no formato do modelo — p/ dar
    contexto da conversa a um operário quando a opção estiver ligada."""
    rows = list(await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    ))
    convo = [
        {"role": m.role, "content": m.content}
        for m in rows
        if m.role in ("user", "assistant") and m.content and not m.compacted
    ]
    return convo[-limit:]


def _make_subagent_runner(
    db: AsyncSession, user: User, chat_id: uuid.UUID | None, max_depth: int,
    pass_context: bool = False, worker_memory: bool = False,
    depth: int = 0, ancestry: frozenset[str] = frozenset(),
):
    """Closure que executa um operário: resolve o ModelConfig e roda um turno aninhado.
    Opções: `pass_context` (dá o histórico do chat ao operário) e `worker_memory` (o
    operário lê/escreve na PRÓPRIA memória). Blinda contra ciclos e recursão profunda."""
    async def run_subagent(key: str, task: str) -> dict:
        if key in ancestry:
            return {"error": "ciclo de subagentes detectado; delegação abortada"}
        try:
            wid = uuid.UUID(str(key))
        except (ValueError, TypeError):
            return {"error": "subagente inválido"}
        mc = await db.get(ModelConfig, wid)
        if mc is None or mc.user_id != user.id or not mc.enabled or not mc.base_model:
            return {"error": "subagente não encontrado ou desabilitado"}
        try:
            api_key, base_url = await _resolve_provider(db, user, mc.base_model)
        except HTTPException as exc:
            return {"error": f"provedor do subagente indisponível: {exc.detail}"}
        sift = await get_sift_for_user(db, user.id, mc)
        skills = await _load_skills(db, user, mc)
        # delegação em cadeia: só se ainda houver profundidade. Os flags de contexto/
        # memória do operário-de-2º-nível vêm da config DELE (ele vira o orquestrador).
        sub_specs: list[dict] = []
        sub_conf: dict = {}
        nested_runner = None
        if depth + 1 < max_depth:
            sub_specs, sub_conf = await _resolve_subagents(db, user, mc)
            if sub_specs:
                nested_runner = _make_subagent_runner(
                    db, user, chat_id, max_depth,
                    pass_context=sub_conf.get("pass_context", False),
                    worker_memory=sub_conf.get("worker_memory", False),
                    depth=depth + 1, ancestry=ancestry | {key},
                )
        # contexto do chat (opt-in)
        history = await _recent_history(db, chat_id) if (pass_context and chat_id) else []
        # memória própria do operário (opt-in): usa a config do modelo do operário
        mem_read: dict | None = None
        mem_write = "off"
        mem_review = False
        if worker_memory and chat_id:
            _stub = SimpleNamespace(memory_config=None)
            mem_read, mem_write, mem_review = _resolve_memory(_stub, mc, user)  # type: ignore[arg-type]
        collected = ""
        usage = None
        try:
            async for ev in run_turn(
                api_key=api_key, model=mc.base_model, history=history, user_text=task,
                chat_system_prompt=mc.system_prompt, params=mc.params or {},
                base_url=base_url, sift=sift, code_mode=_code_mode(mc),
                session=TurnSession(
                    user_id=str(user.id),
                    chat_id=str(chat_id) if chat_id else None,
                    agent_id=_mem_agent_id(mc, mc.base_model),
                ),
                memory=MemoryOpts(read=mem_read, write=mem_write, review=mem_review),
                skills=skills, use_context=True,
                subagent=_subagent_opts(sub_specs, sub_conf, nested_runner),
            ):
                t = ev.get("type")
                if t == "token":
                    collected += ev.get("text", "")
                elif t == "done":
                    collected = ev.get("content") or collected
                    usage = ev.get("usage")
        except Exception as exc:  # noqa: BLE001
            return {"error": f"o subagente falhou: {exc}"}
        # analítica por-agente: registra o uso do operário no ledger
        if usage:
            try:
                rec = _usage_record(usage, mc.base_model, mc)
                async with SessionLocal() as s:
                    uev = usage_event_from_record(user.id, chat_id, uuid.uuid4(), rec)
                    if uev is not None:
                        s.add(uev)
                        await s.commit()
            except Exception:  # noqa: BLE001 - ledger é best-effort
                pass
        return {"kind": "subagent", "agent": mc.name, "output": collected or "(sem resposta)"}

    return run_subagent


# teto de tamanho total dos anexos por turno (defesa; o schema já limita por item)
_MAX_ATTACH_TOTAL = 20 * 1024 * 1024


def _clean_attachments(raw: Any) -> list[dict]:
    """Normaliza os anexos JÁ resolvidos (imagem|arquivo-texto) — sem `data` bruto.

    Usado ao reprocessar anexos salvos (ex.: regenerar), que já têm texto extraído."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    total = 0
    for a in raw[:6]:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        if t in ("image", "audio") and isinstance(a.get("url"), str) and a["url"].startswith("data:"):
            total += len(a["url"])
            if total > _MAX_ATTACH_TOTAL:
                break
            out.append({"type": t, "name": str(a.get("name") or "")[:255], "url": a["url"]})
        elif t == "file" and isinstance(a.get("text"), str) and a["text"]:
            text = a["text"][:200_000]
            total += len(text)
            if total > _MAX_ATTACH_TOTAL:
                break
            out.append({"type": "file", "name": str(a.get("name") or "")[:255], "text": text})
    return out


async def _prepare_attachments(raw: Any, model_config: ModelConfig | None) -> list[dict]:
    """Prepara anexos p/ o turno: mantém imagens, texto direto, e EXTRAI o texto de
    docs binários (PDF/DOCX/XLSX/PPTX) via a integração de extração — descartando o
    base64 depois (não guarda o binário: economia de tokens e de armazenamento).
    A config de extração é POR-MODELO (filter_config.tools.text_extraction)."""
    if not isinstance(raw, list):
        return []
    ex_cfg = tool_config(model_config).get("text_extraction") or {}
    out: list[dict] = []
    total = 0
    for a in raw[:6]:
        if not isinstance(a, dict):
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


def _sse_stream(gen):
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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


