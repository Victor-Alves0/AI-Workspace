"""Rotas de chat: CRUD de chats + endpoint SSE de streaming de mensagens."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_approved
from ..config import get_settings
from ..db import SessionLocal, get_db
from .. import crypto, extraction
from ..models import Artifact, Chat, ChatCompaction, Message, ModelConfig, Skill, User
from ..schemas.chat import (
    ChatCreate,
    ChatDetail,
    ChatOut,
    ChatUpdate,
    MessageEdit,
    MessageOut,
    SendMessageIn,
)
from ..integrations import ollama_service
from ..secrets_service import IMAGEGEN_KEY, OPENROUTER_KEY, get_secret
from ..tools.loader import get_sift_for_user, tool_config
from ..usage_service import usage_event_from_record
from . import artifacts as artifacts_service
from . import generation
from .orchestrator import run_turn, run_turn_guarded
from .titles import generate_title

router = APIRouter(prefix="/chats", tags=["chats"])
logger = logging.getLogger(__name__)


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
    # detalhamento de entrada/saída por categoria (ver orchestrator)
    if isinstance(u.get("input_breakdown"), dict):
        rec["input_breakdown"] = u["input_breakdown"]
    if isinstance(u.get("output_breakdown"), dict):
        rec["output_breakdown"] = u["output_breakdown"]
    if isinstance(u.get("tools_breakdown"), dict):
        rec["tools_breakdown"] = u["tools_breakdown"]
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


@router.get("", response_model=list[ChatOut])
async def list_chats(
    archived: bool = False,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Chat)
        .where(Chat.user_id == user.id, Chat.archived == archived)
        .order_by(Chat.pinned.desc(), Chat.updated_at.desc())
    )
    return list(rows)


@router.post("", response_model=ChatOut)
async def create_chat(
    body: ChatCreate, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = Chat(
        user_id=user.id,
        title=body.title,
        model=body.model,
        system_prompt=body.system_prompt,
        params=body.params,
        folder_id=body.folder_id,
        model_config_id=body.model_config_id,
    )
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return chat


@router.get("/{chat_id}", response_model=ChatDetail)
async def get_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = await _get_owned_chat(db, chat_id, user)
    await db.refresh(chat, attribute_names=["messages"])
    return chat


@router.patch("/{chat_id}", response_model=ChatOut)
async def update_chat(
    chat_id: uuid.UUID,
    body: ChatUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    chat = await _get_owned_chat(db, chat_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(chat, field, value)
    await db.commit()
    await db.refresh(chat)
    return chat


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = await _get_owned_chat(db, chat_id, user)
    await db.delete(chat)
    await db.commit()
    return {"ok": True}


@router.post("/{chat_id}/clone", response_model=ChatOut)
async def clone_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = await _get_owned_chat(db, chat_id, user)
    await db.refresh(chat, attribute_names=["messages"])
    clone = Chat(
        user_id=user.id,
        folder_id=chat.folder_id,
        title=f"{chat.title} (cópia)",
        system_prompt=chat.system_prompt,
        model=chat.model,
        params=chat.params,
    )
    db.add(clone)
    await db.flush()
    for m in chat.messages:
        db.add(
            Message(
                chat_id=clone.id,
                role=m.role,
                content=m.content,
                tool_calls=m.tool_calls,
                tool_call_id=m.tool_call_id,
            )
        )
    await db.commit()
    await db.refresh(clone)
    return clone


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def list_messages(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await _get_owned_chat(db, chat_id, user)
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    return list(rows)


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
                   "read": {"global": True, "model": True, "chat": True}}


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
        return {"global": False, "model": False, "chat": False}, "off", review
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
                user_id=str(user.id), base_url=base_url, sift=sift,
                code_mode=_code_mode(mc), chat_id=str(chat_id) if chat_id else None,
                agent_id=_mem_agent_id(mc, mc.base_model),
                mem_read=mem_read, mem_write=mem_write, mem_review=mem_review,
                skills=skills, use_context=True,
                subagents=sub_specs or None, run_subagent=nested_runner,
                subagent_mode=sub_conf.get("mode", "sequential"),
                subagent_max_calls=sub_conf.get("max_calls", 4),
                subagent_pass_context=sub_conf.get("pass_context", False),
                subagent_worker_memory=sub_conf.get("worker_memory", False),
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
        if t == "image" and isinstance(a.get("url"), str) and a["url"].startswith("data:"):
            total += len(a["url"])
            if total > _MAX_ATTACH_TOTAL:
                break
            out.append({"type": "image", "name": str(a.get("name") or "")[:255], "url": a["url"]})
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
        if t == "image" and isinstance(a.get("url"), str) and a["url"].startswith("data:"):
            total += len(a["url"])
            if total > _MAX_ATTACH_TOTAL:
                break
            out.append({"type": "image", "name": str(a.get("name") or "")[:255], "url": a["url"]})
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


@router.post("/ephemeral")
async def ephemeral(
    body: dict,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    """Chat temporário: streama um turno SEM persistir nada no banco."""
    settings = get_settings()
    model = (body.get("model") or "").strip()
    content = (body.get("content") or "").strip()
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione um modelo")
    if not content and not body.get("attachments"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mensagem vazia")
    if len(content) > settings.max_message_chars:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Mensagem excede {settings.max_message_chars} caracteres",
        )

    api_key, base_url = await _resolve_provider(db, user, model)

    model_config = await _get_model_config(db, body.get("model_config_id"), user)
    genimage = await _genimage_config(db, user, model_config)
    guards = await _resolve_guards(db, user, model_config)  # guardas valem no temporário também
    sift = await get_sift_for_user(db, user.id, model_config)
    skills = await _load_skills(db, user, model_config, body.get("skill_ids") or [])
    attachments = await _prepare_attachments(body.get("attachments"), model_config)
    ocr_on, ocr_eng, ocr_lang = _ocr_prefs(model_config)
    raw_history = body.get("history") or []
    if not isinstance(raw_history, list):
        raw_history = []
    history = [
        {"role": m.get("role"), "content": str(m.get("content"))[: settings.max_message_chars]}
        for m in raw_history[-100:]  # só as últimas 100 mensagens
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    user_id = str(user.id)

    async def event_stream():
        async for event in run_turn_guarded(
            guards=guards,
            api_key=api_key,
            model=model,
            history=history,
            user_text=content,
            chat_system_prompt=body.get("system_prompt"),
            params=body.get("params") or {},
            user_id=user_id,
            user_tz=user_tz,
            base_url=base_url,
            sift=sift,
            code_mode=_code_mode(model_config),
            skills=skills,
            use_context=_use_context(model_config),
            attachments=attachments,
            vision=_has_vision(model_config),
            vision_router_model=_vision_router_model(model_config),
            genimage=genimage,
            image_output=_image_output(model_config),
            ocr=ocr_on,
            ocr_engine=ocr_eng,
            ocr_lang=ocr_lang,
        ):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: SendMessageIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Selecione um modelo no chat")
    if not (body.content or "").strip() and not body.attachments:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mensagem vazia")

    api_key, base_url = await _resolve_provider(db, user, chat.model)

    # histórico atual (antes da nova mensagem) no formato OpenAI
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    history = [
        {"role": m.role, "content": m.content}
        for m in rows
        if m.role in ("user", "assistant") and m.content and not m.compacted
    ]

    # modelo personalizado do chat (define ferramentas + config por-modelo)
    model_config = await _get_model_config(db, chat.model_config_id, user)

    # "@" no promptbox: roteia ESTE turno a outro agente (ModelConfig) sem alterar o
    # padrão do chat. Passa a valer o modelo/prompt/tools/skills DESSE agente.
    agent_override = None
    if body.agent_model_config_id:
        agent_override = await _get_model_config(db, body.agent_model_config_id, user)
        if agent_override is not None and agent_override.base_model:
            model_config = agent_override
        else:
            agent_override = None

    # persiste a mensagem do usuário (extraindo texto de docs; sem guardar o binário)
    attachments = await _prepare_attachments([a.model_dump() for a in body.attachments], model_config)
    user_msg = Message(
        chat_id=chat.id, role="user", content=body.content, attachments=attachments or None
    )
    db.add(user_msg)
    # primeiro título do chat = início da primeira mensagem (fallback instantâneo)
    first_exchange = chat.title == "Novo Chat" and not history
    if first_exchange:
        chat.title = (body.content[:60] or "Anexo")
    await db.commit()

    # geração de título por IA (opt-in em perfil → Interface): só na 1ª troca
    iface = (user.profile or {}).get("interface") if isinstance(user.profile, dict) else None
    iface = iface if isinstance(iface, dict) else {}
    auto_title = first_exchange and bool(iface.get("auto_title"))
    title_model = (iface.get("title_model") or "").strip()
    title_prompt = iface.get("title_prompt") or ""

    # SIFT do usuário, filtrada pelas ferramentas do modelo personalizado do chat
    sift = await get_sift_for_user(db, user.id, model_config)
    skills = await _load_skills(db, user, model_config, body.skill_ids)
    ocr_on, ocr_eng, ocr_lang = _ocr_prefs(model_config)

    # valores efetivos do turno: do agente mencionado (@) ou os padrões do chat
    if agent_override is not None:
        model = agent_override.base_model
        api_key, base_url = await _resolve_provider(db, user, model)
        system_prompt = agent_override.system_prompt
        params = agent_override.params or {}
    else:
        model = chat.model
        system_prompt = chat.system_prompt
        params = chat.params or {}
    user_text = body.content
    user_id = str(user.id)

    arts_on = _artifacts_enabled(user)

    # persiste a resposta e (opt-in) gera o título; roda no driver de background,
    # blindado por `shield` — completa mesmo se o cliente desconectar (F5).
    async def _finish(collected: dict, emit) -> None:
        content_to_save, reasoning = _final_message_fields(collected)
        arts_changed: list[str] = []
        # salva também quando não houve texto mas houve artefato (ex.: imagem nativa)
        if content_to_save or collected["tools"]:
            rec = _usage_record(collected["usage"], model, model_config)
            _flag_budget(rec, model_config, user)
            async with SessionLocal() as s:
                if arts_on and content_to_save:
                    # blocos <artifact> viram linhas versionadas; no texto fica [[artifact:id]]
                    content_to_save, arts_changed = await artifacts_service.extract_and_apply(
                        s, chat_id, user.id, content_to_save
                    )
                m = Message(
                    chat_id=chat_id,
                    role="assistant",
                    content=content_to_save,
                    tokens=rec["total_tokens"] or None,
                    cost=rec["cost"] or None,
                    usage=rec,
                    reasoning=reasoning,
                    tool_events=collected["tools"],
                    memories_used=collected.get("memories"),
                )
                s.add(m)
                await s.flush()
                ev = usage_event_from_record(user.id, chat_id, m.id, rec)
                if ev is not None:
                    s.add(ev)
                await s.commit()
        if arts_changed:
            await emit({"type": "artifacts", "ids": arts_changed})
        # título por IA (1ª troca + opt-in): substitui o fallback de 60 chars.
        if auto_title and collected["content"]:
            new_title = await generate_title(
                api_key, title_model or model, user_text, collected["content"], title_prompt,
                base_url=base_url if not title_model else None,
            )
            if new_title:
                async with SessionLocal() as s:
                    c = await s.get(Chat, chat_id)
                    if c is not None:
                        c.title = new_title
                        await s.commit()
                await emit({"type": "title", "title": new_title})

    genimage = await _genimage_config(db, user, model_config)
    guards = await _resolve_guards(db, user, model_config)
    sub_specs, sub_conf = await _resolve_subagents(db, user, model_config)
    sub_runner = _make_subagent_runner(
        db, user, chat_id, sub_conf.get("max_depth", 2),
        pass_context=sub_conf.get("pass_context", False),
        worker_memory=sub_conf.get("worker_memory", False),
    ) if sub_specs else None
    mem_read, mem_write, mem_review = _resolve_memory(chat, model_config, user)
    mem_banks = _mem_banks(chat, model_config, user)
    source = run_turn_guarded(
        guards=guards,
        api_key=api_key,
        model=model,
        history=history,
        user_text=user_text,
        chat_system_prompt=system_prompt,
        params=params,
        user_id=user_id,
        user_tz=user_tz,
        base_url=base_url,
        extra_system=await _artifacts_extra(db, chat_id, user) if arts_on else None,
        sift=sift,
        code_mode=_code_mode(model_config),
        chat_id=str(chat_id),
        agent_id=_mem_agent_id(model_config, model),
        mem_read=mem_read,
        mem_write=mem_write,
        mem_review=mem_review,
        mem_banks=mem_banks,
        user_profile=_user_profile_dict(user),
        skills=skills,
        use_context=_use_context(model_config),
        attachments=attachments,
        vision=_has_vision(model_config),
        vision_router_model=_vision_router_model(model_config),
        genimage=genimage,
        image_output=_image_output(model_config),
        ocr=ocr_on,
        ocr_engine=ocr_eng,
        ocr_lang=ocr_lang,
        subagents=sub_specs or None,
        run_subagent=sub_runner,
        subagent_mode=sub_conf.get("mode", "sequential"),
        subagent_max_calls=sub_conf.get("max_calls", 4),
        subagent_pass_context=sub_conf.get("pass_context", False),
        subagent_worker_memory=sub_conf.get("worker_memory", False),
    )
    # a geração roda em background (desacoplada da request); a resposta abaixo é
    # só um assinante do buffer. F5/desconexão mata o assinante, não a geração.
    gen = generation.start(str(chat_id), source, _finish)
    return _sse_stream(_subscribe(gen))


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


@router.post("/{chat_id}/stop")
async def stop_generation(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """"Parar" do usuário: cancela a geração em andamento deste chat. O texto já
    transmitido é persistido como resposta parcial (mesmo caminho do shutdown)."""
    await _get_owned_chat(db, chat_id, user)
    gen = generation.get_active(str(chat_id))
    return {"ok": True, "stopped": bool(gen and gen.stop())}


@router.get("/{chat_id}/stream")
async def resume_stream(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Re-assina a geração em andamento de um chat (ex.: o usuário deu F5 no meio
    de uma resposta). Se não houver nada gerando, emite ``idle`` e encerra — o
    front então só carrega as mensagens já persistidas."""
    await _get_owned_chat(db, chat_id, user)
    gen = generation.get_active(str(chat_id))
    if gen is None:

        async def idle():
            yield _sse({"type": "idle"})

        return _sse_stream(idle())
    return _sse_stream(_subscribe(gen))


@router.patch("/{chat_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageEdit,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Edita o conteúdo de uma mensagem (do usuário ou do assistant)."""
    await _get_owned_chat(db, chat_id, user)
    msg = await db.get(Message, message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mensagem não encontrada")
    msg.content = body.content
    await db.commit()
    await db.refresh(msg)
    return msg


@router.delete("/{chat_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Exclui uma mensagem (do usuário ou do assistant)."""
    await _get_owned_chat(db, chat_id, user)
    msg = await db.get(Message, message_id)
    if msg is None or msg.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mensagem não encontrada")
    await db.delete(msg)
    await db.commit()


async def _ordered_messages(db: AsyncSession, chat_id: uuid.UUID) -> list[Message]:
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    return list(rows)


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    """Refaz uma resposta do assistant: descarta essa mensagem (e as posteriores)
    e gera uma nova a partir do mesmo prompt do usuário."""
    chat = await _get_owned_chat(db, chat_id, user)
    api_key, base_url, model_config, sift, skills = await _prepare_turn(db, user, chat)
    genimage = await _genimage_config(db, user, model_config)
    ocr_on, ocr_eng, ocr_lang = _ocr_prefs(model_config)

    rows = await _ordered_messages(db, chat_id)
    idx = next((i for i, m in enumerate(rows) if m.id == message_id), None)
    if idx is None or rows[idx].role not in ("assistant", "user"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mensagem não encontrada")

    if rows[idx].role == "user":
        # "Tentar novamente" NA MENSAGEM DO USUÁRIO (ex.: depois de editá-la):
        # a IA pensa a partir dela — a mensagem fica; tudo que veio depois sai.
        user_text = rows[idx].content
        user_attachments = _clean_attachments(rows[idx].attachments or [])
        if not user_text and not user_attachments:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mensagem vazia")
        history = [
            {"role": m.role, "content": m.content}
            for m in rows[:idx]
            if m.role in ("user", "assistant") and m.content and not m.compacted
        ]
        for m in rows[idx + 1:]:
            await db.delete(m)
        await db.commit()
    else:
        prior = rows[:idx]
        # prompt = última mensagem do usuário antes da resposta
        user_text = ""
        user_attachments = []
        cut = len(prior)
        for i in range(len(prior) - 1, -1, -1):
            if prior[i].role == "user":
                user_text = prior[i].content
                user_attachments = _clean_attachments(prior[i].attachments or [])
                cut = i
                break
        if not user_text:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sem prompt do usuário para refazer")
        history = [
            {"role": m.role, "content": m.content}
            for m in prior[:cut]
            if m.role in ("user", "assistant") and m.content and not m.compacted
        ]

        # remove a resposta e tudo que veio depois
        for m in rows[idx:]:
            await db.delete(m)
        await db.commit()

    model = chat.model
    system_prompt = chat.system_prompt
    params = chat.params or {}
    user_id = str(user.id)
    arts_on = _artifacts_enabled(user)

    async def _finish(collected: dict, emit) -> None:
        content_to_save, reasoning = _final_message_fields(collected)
        arts_changed: list[str] = []
        # salva também quando não houve texto mas houve artefato (ex.: imagem nativa)
        if content_to_save or collected["tools"]:
            rec = _usage_record(collected["usage"], model, model_config)
            _flag_budget(rec, model_config, user)
            async with SessionLocal() as s:
                if arts_on and content_to_save:
                    content_to_save, arts_changed = await artifacts_service.extract_and_apply(
                        s, chat_id, user.id, content_to_save
                    )
                m = Message(
                    chat_id=chat_id,
                    role="assistant",
                    content=content_to_save,
                    tokens=rec["total_tokens"] or None,
                    cost=rec["cost"] or None,
                    usage=rec,
                    reasoning=reasoning,
                    tool_events=collected["tools"],
                    memories_used=collected.get("memories"),
                )
                s.add(m)
                await s.flush()
                ev = usage_event_from_record(user.id, chat_id, m.id, rec)
                if ev is not None:
                    s.add(ev)
                await s.commit()
        if arts_changed:
            await emit({"type": "artifacts", "ids": arts_changed})

    guards = await _resolve_guards(db, user, model_config)
    sub_specs, sub_conf = await _resolve_subagents(db, user, model_config)
    sub_runner = _make_subagent_runner(
        db, user, chat_id, sub_conf.get("max_depth", 2),
        pass_context=sub_conf.get("pass_context", False),
        worker_memory=sub_conf.get("worker_memory", False),
    ) if sub_specs else None
    source = run_turn_guarded(
        guards=guards,
        api_key=api_key,
        model=model,
        history=history,
        user_text=user_text,
        chat_system_prompt=system_prompt,
        params=params,
        user_id=user_id,
        user_tz=user_tz,
        base_url=base_url,
        extra_system=await _artifacts_extra(db, chat_id, user) if arts_on else None,
        sift=sift,
        code_mode=_code_mode(model_config),
        chat_id=str(chat_id),
        agent_id=_mem_agent_id(model_config, model),
        mem_read=(_mem := _resolve_memory(chat, model_config, user))[0],
        mem_write=_mem[1],
        mem_review=_mem[2],
        mem_banks=_mem_banks(chat, model_config, user),
        user_profile=_user_profile_dict(user),
        skills=skills,
        use_context=_use_context(model_config),
        attachments=user_attachments,
        vision=_has_vision(model_config),
        vision_router_model=_vision_router_model(model_config),
        genimage=genimage,
        image_output=_image_output(model_config),
        ocr=ocr_on,
        ocr_engine=ocr_eng,
        ocr_lang=ocr_lang,
        subagents=sub_specs or None,
        run_subagent=sub_runner,
        subagent_mode=sub_conf.get("mode", "sequential"),
        subagent_max_calls=sub_conf.get("max_calls", 4),
        subagent_pass_context=sub_conf.get("pass_context", False),
        subagent_worker_memory=sub_conf.get("worker_memory", False),
    )
    gen = generation.start(str(chat_id), source, _finish)
    return _sse_stream(_subscribe(gen))


@router.post("/{chat_id}/messages/{message_id}/continue")
async def continue_message(
    chat_id: uuid.UUID,
    message_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    """Continua a última resposta do assistant, anexando ao conteúdo existente."""
    chat = await _get_owned_chat(db, chat_id, user)
    api_key, base_url, model_config, sift, skills = await _prepare_turn(db, user, chat)
    genimage = await _genimage_config(db, user, model_config)

    rows = await _ordered_messages(db, chat_id)
    idx = next((i for i, m in enumerate(rows) if m.id == message_id), None)
    if idx is None or rows[idx].role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resposta não encontrada")
    if idx != len(rows) - 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Só é possível continuar a última resposta"
        )

    history = [
        {"role": m.role, "content": m.content}
        for m in rows
        if m.role in ("user", "assistant") and m.content and not m.compacted
    ]
    user_text = (
        "Continue sua resposta anterior exatamente de onde parou, "
        "sem repetir nada do que já foi escrito e sem preâmbulos."
    )

    model = chat.model
    system_prompt = chat.system_prompt
    params = chat.params or {}
    user_id = str(user.id)

    arts_on = _artifacts_enabled(user)

    async def _finish(collected: dict, emit) -> None:
        content = collected["content"] or (collected["streamed"] or "").strip()
        if not content:
            return
        reasoning = collected["reasoning"]
        tools = collected["tools"]
        rec = _usage_record(collected["usage"], model, model_config)
        _flag_budget(rec, model_config, user)
        # ledger: só o DELTA desta continuação (o evento do original já foi gravado);
        # capturado ANTES de `rec` virar cumulativo abaixo.
        delta_ev = usage_event_from_record(user.id, chat_id, message_id, rec)
        arts_changed: list[str] = []
        async with SessionLocal() as s:
            if arts_on:
                content, arts_changed = await artifacts_service.extract_and_apply(
                    s, chat_id, user.id, content
                )
            target = await s.get(Message, message_id)
            if target is not None:
                if delta_ev is not None:
                    s.add(delta_ev)
                sep = "" if target.content.endswith(("\n", " ")) else " "
                target.content = f"{target.content}{sep}{content}"
                old = target.usage or {}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"):
                    rec[k] = int(old.get(k, 0) or 0) + int(rec.get(k, 0) or 0)
                rec["cost"] = float(old.get("cost", 0.0) or 0.0) + rec["cost"]
                # soma os detalhamentos de entrada/saída
                for grp in ("input_breakdown", "output_breakdown"):
                    merged = dict(old.get(grp) or {})
                    for k, v in (rec.get(grp) or {}).items():
                        merged[k] = int(merged.get(k, 0) or 0) + int(v or 0)
                    if merged:
                        rec[grp] = merged
                target.tokens = rec["total_tokens"] or None
                target.cost = rec["cost"] or None
                target.usage = rec
                # anexa o raciocínio da continuação ao existente
                if reasoning:
                    old_r = target.reasoning or {}
                    target.reasoning = {
                        "text": (old_r.get("text", "") + "\n\n" + reasoning["text"]).strip(),
                        "seconds": round(
                            float(old_r.get("seconds", 0) or 0)
                            + float(reasoning.get("seconds", 0) or 0),
                            1,
                        ),
                    }
                # anexa novos usos de ferramenta aos já registrados
                if tools:
                    target.tool_events = (target.tool_events or []) + tools
                await s.commit()
        if arts_changed:
            await emit({"type": "artifacts", "ids": arts_changed})

    source = run_turn(
        api_key=api_key,
        model=model,
        history=history,
        user_text=user_text,
        chat_system_prompt=system_prompt,
        params=params,
        user_id=user_id,
        user_tz=user_tz,
        base_url=base_url,
        extra_system=await _artifacts_extra(db, chat_id, user) if arts_on else None,
        sift=sift,
        code_mode=_code_mode(model_config),
        chat_id=str(chat_id),
        agent_id=_mem_agent_id(model_config, model),
        mem_read=(_mem := _resolve_memory(chat, model_config, user))[0],
        mem_write=_mem[1],
        mem_review=_mem[2],
        mem_banks=_mem_banks(chat, model_config, user),
        user_profile=_user_profile_dict(user),
        skills=skills,
        use_context=_use_context(model_config),
        genimage=genimage,
        image_output=_image_output(model_config),
    )
    gen = generation.start(str(chat_id), source, _finish)
    return _sse_stream(_subscribe(gen))


# --------------------------------------------------------------------------- #
# Compactação de contexto (resumir a conversa para liberar espaço)
# --------------------------------------------------------------------------- #
_COMPACT_SYSTEM = (
    "Você resume conversas preservando o máximo de contexto útil no mínimo de espaço."
)
_COMPACT_INSTRUCTION = (
    "Resuma a conversa abaixo de forma concisa, porém completa, preservando: fatos e "
    "dados importantes, decisões tomadas, preferências e informações sobre o usuário, o "
    "estado atual da tarefa e todo contexto necessário para continuar sem perder nada "
    "relevante. Escreva em tópicos claros. Não invente informações que não estejam na "
    "conversa. Responda apenas com o resumo."
)


@router.post("/{chat_id}/compact")
async def compact_chat(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Compacta o contexto: envia a conversa ao modelo, pede um resumo e substitui
    as mensagens por esse resumo (mantendo o contexto essencial em menos tokens)."""
    from ..providers import openrouter

    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chat sem modelo definido")
    api_key, base_url = await _resolve_provider(db, user, chat.model)

    rows = await _ordered_messages(db, chat_id)
    # só o que ainda está no contexto (não-compactado) entra no resumo — o divisor
    # de resumo (is_summary) já carrega o histórico anterior condensado.
    convo = [
        m for m in rows
        if m.role in ("user", "assistant") and (m.content or "").strip() and not m.compacted
    ]
    if len(convo) < 3:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Conversa curta demais para compactar")

    transcript = "\n\n".join(
        f"{'Usuário' if m.role == 'user' else 'Assistente'}: {m.content}" for m in convo
    )
    messages = [
        {"role": "system", "content": _COMPACT_SYSTEM},
        {"role": "user", "content": f"{_COMPACT_INSTRUCTION}\n\n=== CONVERSA ===\n{transcript}"},
    ]

    # completa sem streaming (acumula o texto do resumo)
    summary = ""
    try:
        async for chunk in openrouter.stream_chat(
            api_key, chat.model, messages, tools=None, params={}, base_url=base_url
        ):
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    summary += delta["content"]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao resumir: {exc}")

    summary = summary.strip()
    if not summary:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "O modelo não retornou um resumo")

    # registra o checkpoint no histórico (timeline) com o SNAPSHOT das mensagens
    # atuais — assim é possível RESTAURAR exatamente este ponto depois.
    # parent_id = checkpoint ativo agora → forma a ÁRVORE de contexto (ramificação).
    parent_id = await db.scalar(
        select(ChatCompaction.id).where(
            ChatCompaction.chat_id == chat_id, ChatCompaction.pinned.is_(True)
        )
    )
    await db.execute(
        update(ChatCompaction).where(ChatCompaction.chat_id == chat_id).values(pinned=False)
    )
    checkpoint = ChatCompaction(
        chat_id=chat_id,
        summary=summary,
        message_count=len(convo),
        pinned=True,
        parent_id=parent_id,
        snapshot=[_serialize_message(m) for m in rows],
    )
    db.add(checkpoint)

    # compactação NÃO-destrutiva: as mensagens permanecem visíveis ao usuário, mas
    # saem do contexto da IA (compacted=True). Um divisor (is_summary) marca o ponto
    # e leva o resumo para o contexto no lugar delas. O que muda é só o que vai ao
    # modelo — não o que o usuário vê. O resumo em si fica no nó do Grafo de contexto.
    await db.execute(
        update(Message)
        .where(Message.chat_id == chat_id, Message.compacted.is_(False))
        .values(compacted=True)
    )
    note = Message(
        chat_id=chat_id,
        role="assistant",
        content=_summary_content(summary),
        is_summary=True,
        compacted=False,
    )
    db.add(note)
    await db.commit()
    await db.refresh(checkpoint)
    return {"ok": True, "summary": summary, "compaction_id": str(checkpoint.id)}


def _summary_content(summary: str) -> str:
    return f"📝 **Resumo da conversa anterior (contexto compactado):**\n\n{summary}"


# --------------------------------------------------------------------------- #
# Mesa-redonda (multi-model chat): modelos conversam entre si, o usuário guia.
# --------------------------------------------------------------------------- #
# chats com uma parada cooperativa pendente (o loop para após o turno atual).
_roundtable_stop: set[str] = set()


class RoundtableRunIn(BaseModel):
    content: str = ""           # injeção do usuário antes de rodar (opcional)
    steps: str = "auto"         # "one" (um turno) | "auto" (várias rodadas)
    next: str | None = None     # id do participante que deve falar (modo manual)


def _rt_speaker(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name") or "Modelo",
        "model": p.get("model"),
        "color": p.get("color"),
    }


def _rt_system(mc: ModelConfig | None, p: dict, names: list[str], self_name: str) -> str:
    frame = (
        f"Você participa de uma conversa em grupo (mesa-redonda) com: {', '.join(names)}. "
        f"Você é {self_name}. Contribua de forma concisa e natural, avançando a discussão. "
        "NÃO escreva as falas dos outros nem prefixe seu próprio nome; mensagens marcadas "
        "com 'Nome:' são dos outros participantes."
    )
    parts = [mc.system_prompt if mc else None, p.get("persona"), frame]
    return "\n\n".join([x for x in parts if x])


def _rt_context(convo: list[dict], names: dict[str, str], target_pid: str) -> tuple[list[dict], str]:
    """Mapeia o transcript compartilhado para a visão de um participante:
    falas próprias = assistant; dos outros = user "Nome: ..."; humano = user."""
    mapped: list[dict] = []
    for c in convo:
        if c["role"] == "user":
            mapped.append({"role": "user", "content": c["content"]})
        elif c.get("speaker") == target_pid:
            mapped.append({"role": "assistant", "content": c["content"]})
        else:
            nm = names.get(c.get("speaker")) or "Participante"
            mapped.append({"role": "user", "content": f"{nm}: {c['content']}"})
    if not mapped:
        return [], "Inicie a conversa."
    return mapped[:-1], mapped[-1]["content"]


def _rt_next_rr(order: list[str], last: str | None) -> str:
    if last in order:
        return order[(order.index(last) + 1) % len(order)]
    return order[0]


async def _rt_moderator(mod: dict, resolved: list[dict], convo: list[dict], names: dict[str, str], user: User, user_tz: str) -> str | None:
    """Pergunta ao moderador (LLM) quem fala em seguida — devolve o id do
    participante, "STOP", ou None (fallback p/ round-robin)."""
    labels = [r["p"].get("name") or "Modelo" for r in resolved]
    transcript = "\n".join(
        f'{(names.get(c.get("speaker")) if c["role"] == "assistant" else "Usuário") or "Usuário"}: {c["content"]}'
        for c in convo
    ) or "(a conversa ainda não começou)"
    sysp = (
        "Você é o moderador de uma mesa-redonda. Participantes: " + ", ".join(labels) + ". "
        "Leia a conversa e responda APENAS com o nome do próximo participante que deve "
        "falar, ou 'STOP' se a conversa já cumpriu seu objetivo ou está repetitiva."
    )
    text = ""
    try:
        async for ev in run_turn(
            api_key=mod["api_key"], model=mod["model"],
            history=[{"role": "user", "content": transcript}],
            user_text="Quem deve falar agora? Responda só o nome, ou STOP.",
            chat_system_prompt=sysp, params={}, user_id=str(user.id), user_tz=user_tz,
            base_url=mod["base_url"], use_tools=False, use_context=True,
        ):
            t = ev.get("type")
            if t == "token":
                text += ev.get("text", "")
            elif t == "done":
                text = ev.get("content") or text
    except Exception:  # noqa: BLE001 - moderador é best-effort
        return None
    ans = text.strip().lower()
    if "stop" in ans:
        return "STOP"
    for r in resolved:
        if (r["p"].get("name") or "").lower() and (r["p"]["name"].lower() in ans):
            return r["p"]["id"]
    return None


async def _rt_persist(chat_id: uuid.UUID, user: User, mc: ModelConfig | None, model: str, sp: dict, text: str, usage: dict | None, reasoning: dict | None) -> uuid.UUID:
    async with SessionLocal() as s:
        rec = _usage_record(usage, model, mc)
        m = Message(
            chat_id=chat_id, role="assistant", content=text or "", speaker=sp,
            reasoning=reasoning, tokens=rec["total_tokens"] or None,
            cost=rec["cost"] or None, usage=rec,
        )
        s.add(m)
        await s.flush()
        uev = usage_event_from_record(user.id, chat_id, m.id, rec)
        if uev is not None:
            s.add(uev)
        await s.commit()
        return m.id


@router.post("/{chat_id}/roundtable/run")
async def roundtable_run(
    chat_id: uuid.UUID,
    body: RoundtableRunIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
    user_tz: str = Depends(_tz_from_header),
):
    chat = await _get_owned_chat(db, chat_id, user)
    participants = list(chat.participants or [])
    if len(participants) < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Adicione participantes à mesa-redonda")
    cfg = chat.roundtable_config or {}
    policy = cfg.get("turn_policy") or "round_robin"
    max_rounds = max(1, min(20, int(cfg.get("max_rounds") or 6)))
    explicit_next = body.next or cfg.get("next")

    # resolve cada participante (provedor + modelo custom + system) ANTES de streamar,
    # pois a `db` da request fecha ao retornar o StreamingResponse.
    resolved: list[dict] = []
    names_list = [p.get("name") or "Modelo" for p in participants]
    for p in participants:
        model = p.get("model") or ""
        if not model:
            continue
        mc = None
        if p.get("model_config_id"):
            try:
                mc = await _get_model_config(db, uuid.UUID(str(p["model_config_id"])), user)
            except (ValueError, TypeError):
                mc = None
        try:
            api_key, base_url = await _resolve_provider(db, user, model)
        except HTTPException:
            continue
        resolved.append({
            "p": p, "mc": mc, "api_key": api_key, "base_url": base_url,
            "system": _rt_system(mc, p, names_list, p.get("name") or "Modelo"),
            "params": (mc.params if mc else {}) or {},
        })
    if not resolved:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhum participante com provedor válido")

    mod = None
    if policy == "moderator":
        mm = (cfg.get("moderator") or {}).get("model")
        if mm:
            try:
                mkey, mbase = await _resolve_provider(db, user, mm)
                mod = {"model": mm, "api_key": mkey, "base_url": mbase}
            except HTTPException:
                mod = None

    # injeção do usuário (guia a conversa) — mensagem role=user, sem speaker
    if (body.content or "").strip():
        um = Message(chat_id=chat.id, role="user", content=body.content)
        db.add(um)
        if chat.title == "Novo Chat":
            chat.title = body.content[:60]
        await db.commit()

    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    convo: list[dict] = []
    for m in rows:
        if m.role in ("user", "assistant") and m.content and not m.compacted:
            convo.append({
                "role": m.role,
                "content": m.content,
                "speaker": (m.speaker or {}).get("id") if m.role == "assistant" else None,
            })
    last_pid = next((c["speaker"] for c in reversed(convo) if c["role"] == "assistant"), None)

    by_id = {r["p"]["id"]: r for r in resolved}
    order = [r["p"]["id"] for r in resolved]
    names_map = {r["p"]["id"]: (r["p"].get("name") or "Modelo") for r in resolved}
    cid = str(chat_id)

    async def _source():
        _roundtable_stop.discard(cid)
        lp = last_pid
        turns = 0
        cap = 1 if body.steps == "one" else max_rounds * max(1, len(resolved))
        try:
            while turns < cap:
                if cid in _roundtable_stop:
                    yield _sse({"type": "roundtable_paused"})
                    break
                pid = None
                if policy == "moderator" and mod is not None:
                    pid = await _rt_moderator(mod, resolved, convo, names_map, user, user_tz)
                    if pid == "STOP":
                        yield _sse({"type": "roundtable_done", "reason": "moderator"})
                        return
                if pid is None or pid not in by_id:
                    if turns == 0 and explicit_next in by_id:
                        pid = explicit_next
                    else:
                        pid = _rt_next_rr(order, lp)
                r = by_id[pid]
                sp = _rt_speaker(r["p"])
                sid = sp["id"]
                yield _sse({"type": "speaker_start", "speaker": sp})
                history, user_text = _rt_context(convo, names_map, sid)
                text = ""
                usage = None
                reasoning_obj = None
                try:
                    async for ev in run_turn(
                        api_key=r["api_key"], model=r["p"]["model"], history=history,
                        user_text=user_text, chat_system_prompt=r["system"], params=r["params"],
                        user_id=str(user.id), user_tz=user_tz, base_url=r["base_url"],
                        use_tools=False, use_context=True, chat_id=cid, mem_write="off",
                    ):
                        t = ev.get("type")
                        if t == "token":
                            text += ev.get("text", "")
                            yield _sse({"type": "token", "text": ev.get("text", ""), "speaker": sid})
                        elif t == "reasoning":
                            yield _sse({"type": "reasoning", "text": ev.get("text", ""), "speaker": sid})
                        elif t == "done":
                            text = ev.get("content") or text
                            usage = ev.get("usage")
                            reasoning_obj = ev.get("reasoning")
                        elif t == "error":
                            yield _sse({"type": "error", "message": ev.get("message", "")})
                except Exception as exc:  # noqa: BLE001 - erro de um turno não derruba a mesa
                    logger.warning("Turno da mesa-redonda falhou: %s", exc)
                    yield _sse({"type": "error", "message": str(exc)})
                mid = await _rt_persist(chat_id, user, r["mc"], r["p"]["model"], sp, text, usage, reasoning_obj)
                convo.append({"role": "assistant", "content": text, "speaker": sid})
                lp = sid
                turns += 1
                yield _sse({"type": "speaker_end", "speaker": sp, "message_id": str(mid)})
                if body.steps == "one":
                    break
        finally:
            _roundtable_stop.discard(cid)
        yield _sse({"type": "roundtable_done"})

    return _sse_stream(_source())


@router.post("/{chat_id}/roundtable/stop")
async def roundtable_stop(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_chat(db, chat_id, user)
    _roundtable_stop.add(str(chat_id))
    return {"ok": True}


def _serialize_message(m: Message) -> dict:
    """Serializa uma mensagem p/ o snapshot do checkpoint (restaurável)."""
    return {
        "role": m.role,
        "content": m.content,
        "tool_calls": m.tool_calls,
        "tool_call_id": m.tool_call_id,
        "tokens": m.tokens,
        "cost": m.cost,
        "usage": m.usage,
        "reasoning": m.reasoning,
        "tool_events": m.tool_events,
        "attachments": m.attachments,
        "is_summary": bool(m.is_summary),
        "compacted": bool(m.compacted),
        "speaker": m.speaker,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


class CompactionOut(BaseModel):
    id: uuid.UUID
    summary: str
    message_count: int
    pinned: bool
    parent_id: uuid.UUID | None = None
    name: str | None = None
    last_message: str | None = None  # preview da última mensagem do snapshot
    created_at: datetime

    class Config:
        from_attributes = True


class CompactionRename(BaseModel):
    name: str | None = Field(default=None, max_length=120)


def _last_message_preview(snapshot: list | None) -> str | None:
    """Preview da última mensagem 'real' do snapshot (p/ busca/exibição na árvore)."""
    if not snapshot:
        return None
    for m in reversed(snapshot):
        if isinstance(m, dict) and not m.get("is_summary"):
            c = (m.get("content") or "").strip()
            if c:
                return c[:140]
    return None


@router.get("/{chat_id}/compactions", response_model=list[CompactionOut])
async def list_compactions(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Grafo de checkpoints do chat (árvore via parent_id; mais antigos primeiro)."""
    await _get_owned_chat(db, chat_id, user)
    rows = list(
        await db.scalars(
            select(ChatCompaction)
            .where(ChatCompaction.chat_id == chat_id)
            .order_by(ChatCompaction.created_at.asc())
        )
    )
    return [
        CompactionOut(
            id=c.id,
            summary=c.summary,
            message_count=c.message_count,
            pinned=c.pinned,
            parent_id=c.parent_id,
            name=c.name,
            last_message=_last_message_preview(c.snapshot),
            created_at=c.created_at,
        )
        for c in rows
    ]


@router.patch("/{chat_id}/compactions/{compaction_id}", response_model=CompactionOut)
async def rename_compaction(
    chat_id: uuid.UUID,
    compaction_id: uuid.UUID,
    body: CompactionRename,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Renomeia (nome personalizado) um checkpoint."""
    await _get_owned_chat(db, chat_id, user)
    cp = await db.get(ChatCompaction, compaction_id)
    if cp is None or cp.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkpoint não encontrado")
    cp.name = (body.name or "").strip()[:120] or None
    await db.commit()
    await db.refresh(cp)
    return CompactionOut(
        id=cp.id, summary=cp.summary, message_count=cp.message_count, pinned=cp.pinned,
        parent_id=cp.parent_id, name=cp.name, last_message=_last_message_preview(cp.snapshot),
        created_at=cp.created_at,
    )


def _restore_snapshot_messages(snapshot: list) -> list[Message]:
    """Reconstrói objetos Message a partir de um snapshot de checkpoint."""
    out: list[Message] = []
    for msg in snapshot or []:
        if not isinstance(msg, dict):
            continue
        m = Message(
            role=msg.get("role") or "assistant",
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls"),
            tool_call_id=msg.get("tool_call_id"),
            tokens=msg.get("tokens"),
            cost=msg.get("cost"),
            usage=msg.get("usage"),
            reasoning=msg.get("reasoning"),
            tool_events=msg.get("tool_events"),
            attachments=msg.get("attachments"),
            is_summary=bool(msg.get("is_summary", False)),
            compacted=bool(msg.get("compacted", False)),
        )
        ca = msg.get("created_at")
        if ca:
            try:
                m.created_at = datetime.fromisoformat(ca)
            except (ValueError, TypeError):
                pass
        out.append(m)
    return out


@router.delete("/{chat_id}/compactions/{compaction_id}")
async def delete_compaction(
    chat_id: uuid.UUID,
    compaction_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Remove um checkpoint do grafo.

    - Checkpoint NÃO ativo: some do grafo, sem tocar na conversa atual.
    - Checkpoint ATIVO (fixado): DESCOMPACTA — restaura o estado que existia antes
      desta compactação (as mensagens voltam ao contexto da IA) e o checkpoint pai
      passa a ser o ativo. É o "desfazer" da última compactação."""
    await _get_owned_chat(db, chat_id, user)
    cp = await db.get(ChatCompaction, compaction_id)
    if cp is None or cp.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkpoint não encontrado")

    if not cp.pinned:
        await db.delete(cp)
        await db.commit()
        return {"ok": True, "undone": False}

    # ativo: descompactar. Sem snapshot (checkpoint legado) não há como restaurar.
    if not cp.snapshot:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Este checkpoint ativo não tem estado salvo para desfazer",
        )
    await db.execute(delete(Message).where(Message.chat_id == chat_id))
    for m in _restore_snapshot_messages(cp.snapshot):
        m.chat_id = chat_id
        db.add(m)
    parent_id = cp.parent_id
    await db.delete(cp)
    if parent_id is not None:
        parent = await db.get(ChatCompaction, parent_id)
        if parent is not None and parent.chat_id == chat_id:
            parent.pinned = True
    await db.commit()
    return {"ok": True, "undone": True}


@router.post("/{chat_id}/compactions/{compaction_id}/pin")
async def restore_compaction(
    chat_id: uuid.UUID,
    compaction_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """RESTAURA um checkpoint: substitui as mensagens atuais pelo snapshot completo
    daquele ponto, trazendo a conversa exatamente como estava antes daquela
    compactação. (Checkpoints antigos sem snapshot: cai no fallback de trocar o
    resumo ativo.)"""
    await _get_owned_chat(db, chat_id, user)
    cp = await db.get(ChatCompaction, compaction_id)
    if cp is None or cp.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compactação não encontrada")

    # RAMIFICAÇÃO: antes de restaurar, salva o estado ATUAL como um novo checkpoint
    # (ramo), para não perder a conversa em que você está — "mantém os dois".
    prev_pinned = await db.scalar(
        select(ChatCompaction.id).where(
            ChatCompaction.chat_id == chat_id, ChatCompaction.pinned.is_(True)
        )
    )
    cur_rows = await _ordered_messages(db, chat_id)
    convo_n = len([m for m in cur_rows if m.role in ("user", "assistant") and (m.content or "").strip()])
    if cur_rows and prev_pinned != cp.id and convo_n > 0:
        cur_snapshot = [_serialize_message(m) for m in cur_rows]
        branch = ChatCompaction(
            chat_id=chat_id,
            summary=_last_message_preview(cur_snapshot) or "Ramo salvo ao restaurar",
            message_count=convo_n,
            pinned=False,
            parent_id=prev_pinned,
            name="Ramo (auto)",
            snapshot=cur_snapshot,
        )
        db.add(branch)

    await db.execute(
        update(ChatCompaction).where(ChatCompaction.chat_id == chat_id).values(pinned=False)
    )
    cp.pinned = True

    snapshot = cp.snapshot or []
    if snapshot:
        # restaura o ponto: apaga o estado atual e recria as mensagens do snapshot
        await db.execute(delete(Message).where(Message.chat_id == chat_id))
        for msg in snapshot:
            if not isinstance(msg, dict):
                continue
            m = Message(
                chat_id=chat_id,
                role=msg.get("role") or "assistant",
                content=msg.get("content") or "",
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                tokens=msg.get("tokens"),
                cost=msg.get("cost"),
                usage=msg.get("usage"),
                reasoning=msg.get("reasoning"),
                tool_events=msg.get("tool_events"),
                attachments=msg.get("attachments"),
                is_summary=bool(msg.get("is_summary", False)),
                compacted=bool(msg.get("compacted", False)),
            )
            ca = msg.get("created_at")
            if ca:
                try:
                    m.created_at = datetime.fromisoformat(ca)
                except (ValueError, TypeError):
                    pass
            db.add(m)
        await db.commit()
        return {"ok": True, "restored": len(snapshot)}

    # fallback (checkpoint legado sem snapshot): troca só a mensagem-resumo do topo
    summary_msg = await db.scalar(
        select(Message)
        .where(Message.chat_id == chat_id, Message.is_summary.is_(True))
        .order_by(Message.created_at)
        .limit(1)
    )
    if summary_msg is not None:
        summary_msg.content = _summary_content(cp.summary)
    else:
        earliest = await db.scalar(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at).limit(1)
        )
        note = Message(
            chat_id=chat_id, role="assistant", content=_summary_content(cp.summary), is_summary=True
        )
        if earliest is not None:
            note.created_at = earliest.created_at - timedelta(seconds=1)
        db.add(note)
    await db.commit()
    return {"ok": True, "restored": 0}


# --------------------------------------------------------------------------- #
# Operações em massa (aba "Controle de Dados" das Configurações)
# --------------------------------------------------------------------------- #
class ChatImportItem(BaseModel):
    title: str = "Chat importado"
    model: str = ""
    system_prompt: str | None = None
    params: dict = {}
    messages: list[dict] = []


class ExportIn(BaseModel):
    # se informado, o export sai CIFRADO com esta senha (AES/Fernet + scrypt)
    password: str | None = Field(default=None, max_length=256)


class ImportIn(BaseModel):
    items: list[ChatImportItem] | None = None
    blob: str | None = None  # export cifrado
    password: str | None = Field(default=None, max_length=256)


@router.post("/bulk/export")
async def export_chats(
    body: ExportIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Exporta todos os chats do usuário. Com senha, devolve um blob cifrado."""
    chats = list(
        await db.scalars(
            select(Chat).where(Chat.user_id == user.id).order_by(Chat.created_at)
        )
    )
    out = []
    for c in chats:
        msgs = await db.scalars(
            select(Message).where(Message.chat_id == c.id).order_by(Message.created_at)
        )
        out.append(
            {
                "title": c.title,
                "model": c.model,
                "system_prompt": c.system_prompt,
                "params": c.params,
                "archived": c.archived,
                "pinned": c.pinned,
                "created_at": c.created_at.isoformat(),
                "messages": [
                    {"role": m.role, "content": m.content, "usage": m.usage}
                    for m in msgs
                ],
            }
        )
    if body.password:
        blob = crypto.encrypt_with_password(
            json.dumps(out, ensure_ascii=False, default=str), body.password
        )
        return {"encrypted": True, "blob": blob}
    return {"encrypted": False, "items": out}


@router.post("/bulk/import")
async def import_chats(
    body: ImportIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Importa chats (formato do export, texto ou cifrado por senha)."""
    items = body.items or []
    if body.blob:
        if not body.password:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este arquivo é cifrado — informe a senha")
        try:
            raw = crypto.decrypt_with_password(body.blob, body.password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        try:
            data = json.loads(raw)
            items = [ChatImportItem(**d) for d in data if isinstance(d, dict)]
        except (ValueError, TypeError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Conteúdo do arquivo inválido")

    created = 0
    for item in items:
        chat = Chat(
            user_id=user.id,
            title=item.title or "Chat importado",
            model=item.model or "",
            system_prompt=item.system_prompt,
            params=item.params or {},
        )
        db.add(chat)
        await db.flush()
        for m in item.messages:
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant", "system", "tool") and content:
                db.add(Message(chat_id=chat.id, role=role, content=content))
        created += 1
    await db.commit()
    return {"ok": True, "imported": created}


@router.post("/bulk/archive-all")
async def archive_all_chats(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await db.execute(
        update(Chat).where(Chat.user_id == user.id).values(archived=True)
    )
    await db.commit()
    return {"ok": True}


@router.post("/bulk/delete-all")
async def delete_all_chats(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await db.execute(delete(Chat).where(Chat.user_id == user.id))
    await db.commit()
    return {"ok": True}
