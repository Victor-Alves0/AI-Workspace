"""Execução de uma chamada de `/v1/chat/completions`.

Dois modos, escolhidos pela própria requisição:

**Plataforma** (padrão) — reusa `chat.run_turn`, então a chamada de API recebe
exatamente o que um chat da interface recebe: system prompt do modelo, ferramentas
executadas no servidor, base de conhecimento, cérebros, skills e memória. É o motivo
de a API existir: o cliente manda uma mensagem e o agente inteiro responde.

**Passthrough** — quando o cliente envia `tools`, ele quer o function calling
clássico (o modelo pede, o CLIENTE executa e devolve `role: "tool"`). Isso é
incompatível com o loop da plataforma, que executa as ferramentas por conta própria
e só devolve o texto final. Nesse caso encaminhamos direto ao provedor, e o
comportamento é o da API OpenAI — sem memória/conhecimento, que dependem do loop.

A escolha é explícita na resposta (`aiworkspace.mode`) para ninguém descobrir por
acidente qual dos dois rodou.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat.orchestrator import MediaOpts, MemoryOpts, TurnSession, run_turn
from ..chat.turn_setup import (
    _code_mode,
    _load_skills,
    _realtime_datetime,
    _resolve_brain,
    _resolve_knowledge,
    _resolve_provider,
    _skill_learning,
    _usage_record,
)
from ..models import ApiRequest, ModelConfig, User
from ..providers import openrouter
from ..tools.loader import get_sift_for_user
from ..usage_service import usage_event_from_record
from . import keys_service, webhooks
from .deps import ApiContext, ApiError

logger = logging.getLogger(__name__)

# tetos de entrada: uma requisição não pode empurrar um histórico ilimitado
MAX_MESSAGES = 200
MAX_ATTACHMENTS = 6


@dataclass
class ResolvedModel:
    """Modelo efetivo da chamada + como falar com o provedor dele."""
    api_key: str
    base_url: str | None
    base_model: str
    config: ModelConfig | None = None
    # id exposto na API: slug do modelo personalizado ou o id do modelo base
    public_id: str = ""


@dataclass
class ParsedRequest:
    """Mensagens OpenAI já quebradas nas partes que o `run_turn` espera."""
    system: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    user_text: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Resolução do modelo
# --------------------------------------------------------------------------- #

async def list_api_models(db: AsyncSession, user: User) -> list[ModelConfig]:
    """Modelos personalizados habilitados do usuário — o catálogo da API.

    A API expõe os PRESETS, não o catálogo cru do OpenRouter: é o preset que carrega
    system prompt, ferramentas e conhecimento. Modelo base solto continua aceito no
    campo `model`, mas não é listado (a lista seria de milhares de itens que a conta
    talvez nem possa usar)."""
    rows = await db.scalars(
        select(ModelConfig)
        .where(ModelConfig.user_id == user.id, ModelConfig.enabled.is_(True))
        .order_by(ModelConfig.name)
    )
    return list(rows)


def model_public_id(mc: ModelConfig) -> str:
    return mc.slug or str(mc.id)


async def resolve_model(
    db: AsyncSession, ctx: ApiContext, requested: str
) -> ResolvedModel:
    """Casa o `model` da requisição com um preset (slug ou id) ou um modelo base."""
    user = ctx.user
    requested = (requested or "").strip() or keys_service.default_model(ctx.key)
    if not requested:
        raise ApiError(
            "Informe o campo 'model'. Liste os disponíveis em GET /v1/models.",
            code="model_required",
        )

    mc: ModelConfig | None = None
    for row in await list_api_models(db, user):
        if requested in (row.slug or "", str(row.id)):
            mc = row
            break
    if mc is None:
        try:
            mid = uuid.UUID(requested)
        except (ValueError, AttributeError):
            mid = None
        if mid is not None:
            found = await db.get(ModelConfig, mid)
            if found is not None and found.user_id == user.id:
                mc = found

    # não é preset: só pode ser um modelo BASE, e todo id de provedor tem a forma
    # "fornecedor/modelo". Sem esta checagem um slug digitado errado viajava até o
    # OpenRouter e voltava como 502 — erro de rede para o que é erro de digitação.
    if mc is None and "/" not in requested:
        raise ApiError(
            f"Modelo '{requested}' não encontrado. Use um dos ids de GET /v1/models "
            "ou um modelo base no formato 'fornecedor/modelo'.",
            status=404, code="model_not_found",
        )

    public = model_public_id(mc) if mc is not None else requested
    if not keys_service.model_allowed(ctx.key, public):
        raise ApiError(
            f"O modelo '{public}' não está liberado para esta chave.",
            status=403, type_="permission_error", code="model_not_allowed",
        )

    base_model = mc.base_model if mc is not None else requested
    if not base_model:
        raise ApiError(f"Modelo '{requested}' não encontrado.", status=404,
                       code="model_not_found")
    api_key, base_url = await _resolve_provider(db, user, base_model)
    return ResolvedModel(api_key=api_key, base_url=base_url, base_model=base_model,
                         config=mc, public_id=public)


# --------------------------------------------------------------------------- #
# Mensagens
# --------------------------------------------------------------------------- #

def _content_text(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Texto + anexos de um `content` OpenAI (string ou lista de partes)."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []
    parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    for p in content:
        if not isinstance(p, dict):
            continue
        kind = p.get("type")
        if kind == "text" and isinstance(p.get("text"), str):
            parts.append(p["text"])
        elif kind == "image_url":
            url = (p.get("image_url") or {}).get("url") if isinstance(p.get("image_url"), dict) else None
            if isinstance(url, str) and url.strip() and len(attachments) < MAX_ATTACHMENTS:
                attachments.append({"type": "image", "name": "image", "url": url.strip()})
        elif kind == "input_audio":
            audio = p.get("input_audio") or {}
            data, fmt = audio.get("data"), (audio.get("format") or "mp3")
            if isinstance(data, str) and data and len(attachments) < MAX_ATTACHMENTS:
                attachments.append({
                    "type": "audio", "name": f"audio.{fmt}",
                    "url": f"data:audio/{fmt};base64,{data}",
                })
    return "\n".join(parts), attachments


def parse_messages(messages: list[dict[str, Any]]) -> ParsedRequest:
    """Quebra as mensagens no formato que `run_turn` espera.

    Os `system` viram um prompt só (concatenados na ordem), a ÚLTIMA mensagem de
    usuário é o turno atual, e o resto é histórico. Mensagens depois da última do
    usuário seriam uma resposta já dada — não há o que gerar a partir delas.
    """
    if not isinstance(messages, list) or not messages:
        raise ApiError("O campo 'messages' é obrigatório e não pode estar vazio.",
                       code="messages_required")
    if len(messages) > MAX_MESSAGES:
        raise ApiError(f"No máximo {MAX_MESSAGES} mensagens por requisição.",
                       code="too_many_messages")

    systems: list[str] = []
    turns: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            raise ApiError("Cada item de 'messages' deve ser um objeto.",
                           code="invalid_message")
        role = m.get("role")
        text, attachments = _content_text(m.get("content"))
        if role == "system" or role == "developer":
            if text:
                systems.append(text)
        elif role in ("user", "assistant"):
            turns.append({"role": role, "content": text, "_att": attachments})
        elif role == "tool":
            raise ApiError(
                "Mensagens 'tool' só valem junto do campo 'tools' (function calling "
                "do cliente).", code="unexpected_tool_message",
            )

    last_user = next((i for i in range(len(turns) - 1, -1, -1) if turns[i]["role"] == "user"), None)
    if last_user is None:
        raise ApiError("É preciso ao menos uma mensagem com role 'user'.",
                       code="user_message_required")

    current = turns[last_user]
    history = [{"role": t["role"], "content": t["content"]}
               for t in turns[:last_user] if t["content"]]
    return ParsedRequest(
        system="\n\n".join(systems),
        history=history,
        user_text=current["content"],
        attachments=current["_att"],
    )


# --------------------------------------------------------------------------- #
# Memória (política da chave -> escopos do mem0)
# --------------------------------------------------------------------------- #

def memory_opts(key: Any, end_user: str | None) -> tuple[MemoryOpts, str | None, str | None]:
    """(MemoryOpts, agent_id, run_id) conforme o modo de memória da chave.

    O mem0 tem três eixos — usuário, agente e sessão — então cada modo vira uma
    combinação deles em vez de um banco separado:

      none / request -> nada é lido nem escrito
      persistent     -> memória GLOBAL do usuário (a mesma da interface)
      shared         -> escopo comum a todas as chaves (`api:shared`), fora do app
      key            -> escopo exclusivo da chave (`api:<id>`)
      end_user       -> escopo por usuário final, dentro da chave
    """
    mode = keys_service.memory_mode(key)
    off = {"global": False, "model": False, "chat": False, "project": False}
    if mode in ("none", "request"):
        return MemoryOpts(read=off, write="off"), None, None
    if mode == "persistent":
        return MemoryOpts(read={**off, "global": True}, write="global"), None, None
    if mode == "shared":
        return MemoryOpts(read={**off, "model": True}, write="model"), "api:shared", None
    if mode == "key":
        return MemoryOpts(read={**off, "model": True}, write="model"), f"api:{key.id}", None
    if not end_user:
        raise ApiError(
            "Esta chave usa memória por usuário final: envie o campo 'user' com o "
            "identificador de quem está falando.", code="end_user_required",
        )
    return (MemoryOpts(read={**off, "chat": True}, write="chat"),
            f"api:{key.id}", f"api:{key.id}:{end_user}")


# --------------------------------------------------------------------------- #
# Turno
# --------------------------------------------------------------------------- #

_SAFE_PARAMS = (
    "temperature", "top_p", "top_k", "max_tokens", "presence_penalty",
    "frequency_penalty", "seed", "stop", "reasoning", "response_format",
)


def build_params(rm: ResolvedModel, body: dict[str, Any]) -> dict[str, Any]:
    """Parâmetros de geração: os do preset, sobrescritos pelos da requisição.

    Só a allowlist passa — `model`, `messages` e `stream` são resolvidos pelo
    servidor e não podem ser redefinidos por um campo solto do corpo.
    """
    params = dict((rm.config.params if rm.config else None) or {})
    for name in _SAFE_PARAMS:
        if body.get(name) is not None:
            params[name] = body[name]
    if body.get("max_completion_tokens") is not None:  # nome novo da OpenAI
        params["max_tokens"] = body["max_completion_tokens"]
    return params


async def run_platform_turn(
    ctx: ApiContext, rm: ResolvedModel, parsed: ParsedRequest, body: dict[str, Any]
):
    """Gera os eventos de `run_turn` com tudo o que o preset do usuário define."""
    db, user = ctx.db, ctx.user
    mc = rm.config
    tools_off = body.get("tool_choice") == "none"

    sift = None if tools_off else await get_sift_for_user(db, user.id, mc)
    skills = await _load_skills(db, user, mc)
    mem, agent_id, run_id = memory_opts(ctx.key, body.get("user"))

    system = mc.system_prompt if mc else None
    if parsed.system:
        # o system do cliente COMPLEMENTA o do preset (não substitui): o preset é a
        # identidade que o dono da chave configurou, e uma chamada de API não deveria
        # conseguir apagá-la mandando o próprio system.
        system = f"{system}\n\n{parsed.system}" if system else parsed.system

    knowledge = _resolve_knowledge(None, mc, user)
    brain = _resolve_brain(None, mc, user)
    tz = str((user.profile or {}).get("timezone") or "")

    async for event in run_turn(
        api_key=rm.api_key,
        base_url=rm.base_url,
        model=rm.base_model,
        history=parsed.history,
        user_text=parsed.user_text,
        chat_system_prompt=system,
        params=build_params(rm, body),
        session=TurnSession(
            user_id=str(user.id), user_tz=tz, chat_id=run_id, agent_id=agent_id,
            background=True,  # sem UI para confirmar nada: guardas não interrogam
        ),
        sift=sift,
        use_tools=not tools_off,
        code_mode=False if tools_off else _code_mode(mc),
        skills=skills,
        use_context=True,
        knowledge=knowledge,
        brain=brain,
        skill_learning=False,  # propor skill exige aprovação humana; API não tem
        realtime_datetime=_realtime_datetime(mc),
        memory=mem,
        media=MediaOpts(
            attachments=parsed.attachments or None,
            vision=bool(mc and (mc.capabilities or {}).get("vision")),
        ),
    ):
        yield event


async def run_passthrough(rm: ResolvedModel, messages: list[dict], body: dict[str, Any]):
    """Encaminha ao provedor com as `tools` do cliente e devolve os chunks crus."""
    async for chunk in openrouter.stream_chat(
        rm.api_key, rm.base_model, messages,
        tools=body.get("tools") or None,
        params=build_params(rm, body),
        base_url=rm.base_url,
    ):
        yield chunk


# --------------------------------------------------------------------------- #
# Contabilidade
# --------------------------------------------------------------------------- #

async def record(
    db: AsyncSession, ctx: ApiContext, *, endpoint: str, model: str,
    status: int = 200, error: str = "", started: float = 0.0,
    usage: dict[str, Any] | None = None, rec: dict[str, Any] | None = None,
) -> None:
    """Grava o log da requisição e — quando houve consumo — o ledger de uso.

    São dois registros porque respondem a perguntas diferentes: `api_requests` diz
    o que ACONTECEU (inclusive 429 e erro do provedor), `usage_events` diz o que foi
    COBRADO e é de onde saem orçamento e analítica.
    """
    latency = int((time.perf_counter() - started) * 1000) if started else 0
    u = usage or {}
    db.add(ApiRequest(
        user_id=ctx.user.id, api_key_id=ctx.key.id, key_name=ctx.key.name or "",
        endpoint=endpoint[:64], model=(model or "")[:255], status=status,
        error=(error or "")[:255], latency_ms=latency, ip=(ctx.ip or "")[:64],
        prompt_tokens=int(u.get("prompt_tokens") or 0),
        completion_tokens=int(u.get("completion_tokens") or 0),
        total_tokens=int(u.get("total_tokens") or 0),
        cost=float(u.get("cost") or 0.0),
    ))
    if rec:
        ev = usage_event_from_record(ctx.user.id, None, None, rec)
        if ev is not None:
            ev.api_key_id = ctx.key.id
            db.add(ev)
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - log não pode derrubar a resposta
        logger.warning("falha ao registrar requisição de API: %s", exc)
        await db.rollback()
        return
    await _budget_alerts(db, ctx)


async def _budget_alerts(db: AsyncSession, ctx: ApiContext) -> None:
    """Dispara webhook nos marcos de 50/80/100% do orçamento da chave."""
    from . import limits

    if not (ctx.key.webhook or {}).get("url"):
        return
    try:
        win = await limits.usage_window(db, ctx.key)
        crossed = limits.budget_alerts(ctx.key, win["cost_month"])
        if not crossed:
            return
        for mark in crossed:
            webhooks.emit(ctx.key, "budget.alert", {
                "percent": mark, "cost_month": round(win["cost_month"], 6),
                "budget_usd": (ctx.key.limits or {}).get("budget_usd"),
            })
        ctx.key.alerts_sent = sorted({*(ctx.key.alerts_sent or []), *crossed})
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("alerta de orçamento falhou: %s", exc)
        await db.rollback()


def usage_record_for(rm: ResolvedModel, usage: dict | None) -> dict[str, Any]:
    return _usage_record(usage, rm.base_model, rm.config)
