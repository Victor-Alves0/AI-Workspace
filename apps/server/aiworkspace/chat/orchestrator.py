"""Loop agêntico de um turno de chat.

Sequência por turno (ver plano):
  1. mem0.search  -> memórias relevantes injetadas no system
  2. monta system = system_prompt do chat + SIFT system_prompt + bloco de memória
     e tools = SIFT.openai_tools() (as 3 meta-tools)
  3. stream do OpenRouter -> emite eventos `token`
  4. se houver tool_calls -> dispatch (SIFT) em threadpool, anexa role=tool, repete (3)
  5. resposta final -> emite `done`; persistência e mem0.add ficam a cargo da rota

Eventos emitidos (dicts): type ∈ {token, tool_call, tool_result, usage, done, error}.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.concurrency import run_in_threadpool

from .. import extraction
from ..config import get_settings
from ..db import SessionLocal
from ..memory import mem0_service
from ..models import GeneratedImage
from ..providers import image_gen, openrouter
from ..tools import toolctx

logger = logging.getLogger(__name__)

# resposta "vazia de verdade": só espaços e/ou marcadores de posicionamento de
# artefato ([[research]], [[chart]], …) — usado pela cutucada final do loop
_MARKER_ONLY_RE = re.compile(
    r"^(?:\s|\[\[(?:canvas|diagram|chart|quote|stock|research|image|email)\]\])*$", re.IGNORECASE
)

# Tarefas em background (fora do caminho crítico da resposta). Guardamos as refs
# para o asyncio não coletá-las antes de terminarem.
_bg_tasks: set[asyncio.Task] = set()


def _spawn_memory_write(
    api_key: str, user_text: str, assistant_text: str, user_id: str,
    chat_id: str, agent_id: str | None, scope: str, review: bool = False,
) -> None:
    """Grava a memória pós-turno no escopo escolhido (global/model/chat), SEM
    bloquear a conclusão do turno.

    A extração do mem0 é um LLM call (~3s) + embeddings + inserts no pgvector.
    Rodá-la antes do ``done`` fazia a UI segurar a resposta como "não concluída"
    por segundos a cada turno. Aqui ela roda desacoplada; é best-effort (já
    degrada para no-op em falha), então não precisa de shield."""
    async def _write() -> None:
        try:
            await run_in_threadpool(
                lambda: mem0_service.add_scoped(
                    api_key,
                    [
                        {"role": "user", "content": user_text},
                        {"role": "assistant", "content": assistant_text},
                    ],
                    user_id,
                    scope=scope,
                    chat_id=chat_id,
                    agent_id=agent_id,
                    review=review,
                )
            )
        except Exception:  # noqa: BLE001 - memória é best-effort
            logger.exception("memória pós-turno falhou (chat %s)", chat_id)

    task = asyncio.create_task(_write())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# Prompt "quando usar" padrão do modo SIFT="prompt": um empurrão curto e barato
# (em vez de despejar o catálogo). SIFT resolve o "COMO usar" (descoberta via
# search_tools); isto resolve o "QUANDO usar". Editável por modelo.
DEFAULT_TOOL_PROMPT = (
    "To avoid hallucinations: for ANY current/exact/private information "
    "(user's data, time, weather, email, calendar, real-time data), ALWAYS use a "
    "tool — never guess. If unsure whether a tool exists, call search_tools. Only "
    "answer from your own knowledge for general/creative questions."
)

# Injetado SEMPRE (não sobrescrito pelo prompt custom do modelo) e SEM enumerar
# ferramentas — mantém a premissa do SIFT (descoberta sob demanda, tokens mínimos).
# Corrige o bug do "já apaguei a luz 😊" sem chamar tool: em pedidos de AÇÃO o modelo
# é obrigado a passar pela descoberta/execução, e proibido de confirmar sem resultado.
TOOL_ACTION_GUARD = (
    "CRITICAL — real-world actions: when the user asks you to DO something (control a "
    "device, send/organize email, create or change calendar events, set reminders or "
    "monitors), you MUST perform it through a tool and wait for its result before "
    "confirming. If you haven't located a suitable tool yet, call search_tools FIRST — "
    "acting without a tool is impossible. NEVER say an action was done, sent or scheduled "
    "unless a tool actually returned success; if no tool covers it, say you can't do it."
)

# O índice de descoberta é inglês-first (embedder EN + descrições em EN). Queries no
# idioma do usuário (ex.: "apagar luz do quarto") rankeavam mal; traduzir a query é
# grátis p/ o modelo e conserta a busca p/ QUALQUER tool — com fallback p/ tools que
# o usuário descreveu em outro idioma (o lado BM25 do híbrido casa lexicalmente).
SEARCH_LANG_HINT = (
    "search_tools queries: ALWAYS write them in English (the tool index is English-first) "
    "— translate the user's request, e.g. 'apagar a luz do quarto' → 'turn off the bedroom "
    "light'. Only if nothing relevant comes back, retry once in the user's own language "
    "(user-created tools may be described in it)."
)


def _compose_tool_prompt(base: str, catalog: list[str], mode: str, custom: str, meta: str) -> str:
    """Monta a seção de ferramentas do system prompt.

    Premissa SIFT preservada: o catálogo legível só entra no modo "list" (opt-in,
    explícito e mais caro); no modo "prompt" o modelo descobre via `search_tools`.
    O `TOOL_ACTION_GUARD` é sempre anexado (barato e não-enumerativo)."""
    parts: list[str] = [base] if base else []
    if mode == "list" and catalog:
        listing = "\n".join(f"- {c}" for c in catalog)
        parts.append(
            "## Ferramentas disponíveis\n"
            "Você TEM acesso às ferramentas abaixo (acesse-as via as meta-ferramentas "
            f"`search_tools` + `{meta}`); elas já estão ATIVAS — não diga que precisam "
            f"ser ativadas:\n{listing}\n"
            "Sempre que a tarefa exigir data/hora, cálculos, informações da web, dados "
            "do usuário ou uma ação num dispositivo, USE a ferramenta correspondente."
        )
    parts.append(f"## Quando usar ferramentas\n{custom}\n\n{TOOL_ACTION_GUARD}\n\n{SEARCH_LANG_HINT}")
    return "\n\n".join(parts)


# Provedores cujo prompt caching é EXPLÍCITO no OpenRouter (marca-se um
# "breakpoint" com cache_control numa parte do conteúdo). Nos demais (OpenAI,
# DeepSeek, Grok, …) o cache é automático e não requer marcação.
_EXPLICIT_CACHE_PROVIDERS = ("anthropic", "google")


def _build_static_system(chat_system_prompt: str | None, sift_prompt: str) -> str:
    """Parte ESTÁVEL do system (prompt do chat + SIFT) — o prefixo cacheável."""
    parts: list[str] = []
    if chat_system_prompt:
        parts.append(chat_system_prompt.strip())
    if sift_prompt:
        parts.append(sift_prompt)
    return "\n\n".join(p for p in parts if p)


_VISION_DESCRIBE_PROMPT = (
    "Descreva em detalhe o conteúdo das imagens a seguir para que outro assistente, "
    "que NÃO pode vê-las, entenda tudo que é relevante: textos (transcreva-os), "
    "objetos, pessoas, gráficos, números, cores e qualquer detalhe importante. "
    "Seja objetivo e completo."
)


def _decode_data_url(url: str | None) -> bytes | None:
    """Bytes de um data URL de imagem (data:image/...;base64,XXXX)."""
    if not url or not isinstance(url, str) or "," not in url:
        return None
    try:
        return base64.b64decode(url.split(",", 1)[1], validate=False)
    except Exception:  # noqa: BLE001
        return None


async def _describe_images(
    api_key: str, model: str, images: list[dict[str, Any]]
) -> str:
    """Vision Router: pede a um modelo COM visão que descreva as imagens em texto,
    para um modelo sem visão poder 'entender' o que foi enviado (lazy multimodal)."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": _VISION_DESCRIBE_PROMPT}]
    for a in images:
        parts.append({"type": "image_url", "image_url": {"url": a["url"]}})
    text = await openrouter.complete(api_key, model, [{"role": "user", "content": parts}])
    return (text or "").strip()


def _view_skill_tool() -> dict[str, Any]:
    """Meta-ferramenta que entrega o conteúdo completo de uma skill sob demanda."""
    return {
        "type": "function",
        "function": {
            "name": "view_skill",
            "description": (
                "Carrega o conteúdo completo (instruções passo a passo) de uma skill "
                "equipada, pelo seu identificador (slug). Chame ANTES de responder "
                "sempre que uma skill listada em '## Skills disponíveis' for útil para "
                "a tarefa — você recebe só nome+descrição até chamar esta ferramenta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "identificador da skill, ex.: revisao_de_codigo",
                    }
                },
                "required": ["slug"],
            },
        },
    }


def _delegate_tool(agents: list[dict[str, Any]]) -> dict[str, Any]:
    """Tool injetada quando o modelo pode usar SUBAGENTES: delega uma sub-tarefa a
    um dos agentes (operários) autorizados, que executa com o próprio prompt/tools
    e devolve o resultado. O `agent` deve ser uma das chaves listadas."""
    lines = "\n".join(f"- {a['key']}: {a['name']} — {a.get('description') or ''}".rstrip(" —") for a in agents)
    keys = [a["key"] for a in agents]
    return {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": (
                "Delegue uma sub-tarefa a um subagente especializado (operário), que a "
                "executa isoladamente e devolve o resultado para você sintetizar. Use "
                "quando outro agente for mais adequado para parte do trabalho. Agentes "
                f"disponíveis:\n{lines}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": keys, "description": "chave do subagente a chamar"},
                    "task": {"type": "string", "description": "instrução completa e autossuficiente para o subagente"},
                },
                "required": ["agent", "task"],
            },
        },
    }


def _generate_image_tool() -> dict[str, Any]:
    """Tool injetada quando o filtro GenImage Router está ativo: gera uma imagem a
    partir de um prompt, roteando p/ o modelo de imagem configurado."""
    return {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate an image from a text prompt and show it to the user. Use whenever "
                "the user asks to create/draw/generate a picture, logo, illustration, etc. "
                "Write a rich, detailed English `prompt`. The image is displayed automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "detailed image description (English works best)"},
                    "size": {"type": "string", "description": "optional, e.g. 1024x1024, 1792x1024"},
                },
                "required": ["prompt"],
            },
        },
    }


async def _save_generated_image(
    user_id: str, chat_id: str | None, mime: str, data: bytes, prompt: str, model: str
) -> str:
    """Guarda os bytes da imagem e devolve o id (str). Roda no loop da request."""
    async with SessionLocal() as db:
        row = GeneratedImage(
            user_id=uuid.UUID(user_id),
            chat_id=uuid.UUID(chat_id) if chat_id else None,
            mime=mime,
            data=data,
            prompt=prompt[:2000],
            model=model,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return str(row.id)


def _skills_block(skills: list[dict[str, Any]]) -> str:
    """Bloco (estável, cacheável) que lista as skills — só nome+descrição.

    O conteúdo completo NÃO vai aqui: o modelo o carrega sob demanda via
    view_skill (lazy loading), mantendo o prompt barato quando não é preciso."""
    if not skills:
        return ""
    lines = [
        f"- `{s['slug']}` — {s['name']}"
        + (f": {s['description']}" if s.get("description") else "")
        for s in skills
    ]
    return (
        "## Skills disponíveis\n"
        "Você tem acesso às skills abaixo (mostradas apenas com nome e descrição). "
        "Quando uma for útil para a tarefa, chame `view_skill` com o `slug` dela para "
        "carregar as instruções completas ANTES de responder — não invente o conteúdo.\n"
        + "\n".join(lines)
    )


def _memory_block(memories: list[str]) -> str:
    """Bloco de memórias (VARIA a cada turno) — fica fora do trecho cacheado."""
    if not memories:
        return ""
    block = "\n".join(f"- {m}" for m in memories)
    return "Memórias relevantes sobre o usuário (use quando útil):\n" + block


def _temporal_note(user_tz: str, tz_offset: int | None = None) -> str:
    """Linha de contexto temporal: data/hora atual NO FUSO do usuário. Sem isso o
    modelo raciocina em UTC (relógio do servidor) e erra 'daqui a X min' / 'às HH:MM'
    e a criação de eventos de agenda. Preferimos o fuso IANA (`user_tz`, vindo do
    navegador); na falta dele usamos `tz_offset` (getTimezoneOffset em minutos:
    UTC = local + offset — usado por automações agendadas, que não têm navegador).
    Sem nenhum dos dois → cai em UTC."""
    now_utc = datetime.now(timezone.utc)
    tzname = "UTC"
    local = now_utc
    if user_tz:
        try:
            local = now_utc.astimezone(ZoneInfo(user_tz))
            tzname = user_tz
        except Exception:  # noqa: BLE001
            local, tzname = now_utc, "UTC"
    elif tz_offset is not None:
        try:
            local = now_utc.astimezone(timezone(timedelta(minutes=-int(tz_offset))))
            tzname = "fuso local"
        except Exception:  # noqa: BLE001
            local, tzname = now_utc, "UTC"
    off = local.strftime("%z")  # ex.: -0300
    off_fmt = f", UTC{off[:3]}:{off[3:]}" if off else ""
    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    return (
        f"Agora (fuso do usuário): {dias[local.weekday()]}, {local.strftime('%Y-%m-%d %H:%M')} "
        f"({tzname}{off_fmt}). Interprete SEMPRE horários neste fuso — inclusive ao criar "
        "lembretes e eventos de agenda (não use UTC)."
    )


def _system_message(static_system: str, mem_block: str, model: str, time_note: str = "") -> dict[str, Any]:
    """Monta a mensagem `system`. Para provedores de cache explícito, marca a
    parte estável com cache_control e deixa memória + hora depois (sem cache), para
    que o prefixo grande e repetido seja de fato reaproveitado entre turnos (a hora
    muda a cada minuto — jamais pode entrar no prefixo cacheado)."""
    tail = "\n\n".join([b for b in (time_note, mem_block) if b])
    prov = model.split("/", 1)[0].lower()
    if prov in _EXPLICIT_CACHE_PROVIDERS and static_system:
        parts: list[dict[str, Any]] = [
            {"type": "text", "text": static_system, "cache_control": {"type": "ephemeral"}}
        ]
        if tail:
            parts.append({"type": "text", "text": "\n\n" + tail})
        return {"role": "system", "content": parts}
    full = static_system + (("\n\n" + tail) if tail else "")
    return {"role": "system", "content": full}


def _merge_usage(total: dict[str, float], usage: dict) -> None:
    """Soma um bloco de usage (tokens/custo) ao acumulado do turno."""
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = usage.get(k)
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
    # tokens de raciocínio ("thinking"), quando o modelo os reporta
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        rt = details.get("reasoning_tokens")
        if isinstance(rt, (int, float)):
            total["reasoning_tokens"] = total.get("reasoning_tokens", 0) + rt
    # tokens de ENTRADA lidos do cache do provedor (prompt caching)
    pdetails = usage.get("prompt_tokens_details")
    if isinstance(pdetails, dict):
        ct = pdetails.get("cached_tokens")
        if isinstance(ct, (int, float)):
            total["cached_tokens"] = total.get("cached_tokens", 0) + ct
    cost = usage.get("cost")
    if cost is None and isinstance(usage.get("cost_details"), dict):
        cost = usage["cost_details"].get("upstream_inference_cost")
    if isinstance(cost, (int, float)):
        total["cost"] = total.get("cost", 0.0) + cost


def _accumulate_tool_calls(buffer: dict[int, dict], deltas: list[dict]) -> None:
    """Agrega fragmentos de tool_calls vindos do streaming (formato OpenAI)."""
    for d in deltas:
        idx = d.get("index", 0)
        slot = buffer.setdefault(
            idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]


async def run_turn(
    *,
    api_key: str,
    model: str,
    history: list[dict[str, Any]],
    user_text: str,
    chat_system_prompt: str | None,
    params: dict[str, Any],
    user_id: str,
    user_tz: str = "",
    user_tz_offset: int | None = None,
    background: bool = False,
    base_url: str | None = None,
    sift: Any | None = None,
    use_tools: bool = True,
    code_mode: bool = False,
    chat_id: str | None = None,
    agent_id: str | None = None,
    mem_read: dict[str, bool] | None = None,
    mem_write: str = "off",
    mem_review: bool = False,
    mem_banks: list[str] | None = None,
    user_profile: dict[str, Any] | None = None,
    skills: list[dict[str, Any]] | None = None,
    use_context: bool = True,
    attachments: list[dict[str, Any]] | None = None,
    vision: bool = False,
    vision_router_model: str | None = None,
    ocr: bool = False,
    ocr_engine: str = "tesseract",
    ocr_lang: str = "por+eng",
    genimage: dict[str, Any] | None = None,
    image_output: bool = False,
    extra_system: str | None = None,
    subagents: list[dict[str, Any]] | None = None,
    run_subagent: Any | None = None,
    subagent_mode: str = "sequential",
    subagent_max_calls: int = 4,
    subagent_pass_context: bool = False,
    subagent_worker_memory: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    settings = get_settings()

    # Raciocínio: "Desligado" na UI apaga a chave => sem isto, modelos híbridos
    # (DeepSeek V4 etc.) raciocinam POR PADRÃO e o usuário vê "Pensando…" com o
    # seletor em off. Ausência de config = desligado de verdade. Quem escolhe um
    # esforço na UI manda {"effort": ...} e passa intacto.
    params = dict(params or {})
    auto_reasoning_off = "reasoning" not in params
    if auto_reasoning_off:
        params["reasoning"] = {"enabled": False}

    # torna o chat atual visível às ferramentas (ex.: lembrete "no chat atual").
    # contextvar é isolado por task async e herdado pelo threadpool do dispatch.
    toolctx.current_chat_id.set(chat_id)
    # fuso do usuário visível às ferramentas (lembrete/agenda interpretam horários locais)
    toolctx.user_tz.set(user_tz or "")
    # execução autônoma (automação): tools pulam fases interativas (ex.: revisão de e-mail)
    toolctx.background.set(bool(background))
    # perfil do usuário visível à tool user.profile.get (nome, sobre, nascimento…)
    toolctx.user_profile.set(user_profile or {})

    # 1. memória — UNIÃO dos escopos ligados em `mem_read` ({global, model, chat}).
    # Leak-safe: só global (compartilhado) + as do modelo atual + as deste chat —
    # nunca de outros chats/modelos. Ver memory/mem0_service.search_for_turn.
    mem_items: list[dict[str, str]] = []
    if (mem_read and any(mem_read.values())) or mem_banks:
        mem_items = await run_in_threadpool(
            lambda: mem0_service.search_for_turn(
                api_key, user_text, user_id,
                chat_id=chat_id, agent_id=agent_id,
                read=mem_read or {}, banks=mem_banks, limit=6,
            )
        )
    memories = [m["text"] for m in mem_items]
    if mem_items:
        yield {"type": "memory", "count": len(mem_items), "items": mem_items}

    # 2. montagem — em code mode o modelo recebe run_code (orquestra várias
    # tools escrevendo Python numa chamada só) em vez de execute_tool
    has_tools = sift is not None and use_tools
    if has_tools and code_mode:
        sift_prompt = sift.code_system_prompt
        tools = list(sift.code_tools())
        # tools LONGAS promovidas a 1ª classe (rodam fora do sandbox do run_code —
        # o watchdog de parede mataria o filho e descartaria o resultado)
        extra = getattr(sift, "_aw_code_extra_tools", None)
        if extra:
            tools += list(extra)
    elif has_tools:
        sift_prompt = sift.system_prompt
        # usa os specs já capturados COM as ferramentas fixadas (pin), quando houver
        tools = getattr(sift, "_aw_openai_tools", None) or sift.openai_tools()
    else:
        sift_prompt = ""
        tools = []

    # Como o SIFT é apresentado ao modelo — o "QUANDO usar" (o "COMO usar" já é
    # resolvido pelas meta-ferramentas). O catálogo é injetado nos dois modos (o modelo
    # precisa saber o que tem); "list" reforça o texto. O guard de ação é sempre anexado.
    if has_tools:
        mode = getattr(sift, "_aw_sift_mode", "prompt")
        catalog = getattr(sift, "_aw_tools", None) or []
        meta = "run_code" if code_mode else "execute_tool"
        custom = (getattr(sift, "_aw_sift_prompt", "") or "").strip() or DEFAULT_TOOL_PROMPT
        sift_prompt = _compose_tool_prompt(sift_prompt, catalog, mode, custom, meta)

    # Skills (independentes do SIFT): o modelo vê só nome+descrição e carrega o
    # conteúdo completo sob demanda via view_skill. Adiciona a meta-ferramenta
    # mesmo sem SIFT — basta ter ao menos uma skill equipada/invocada.
    skills = skills or []
    skills_by_slug: dict[str, dict[str, Any]] = {}
    if skills:
        tools = list(tools) + [_view_skill_tool()]
        for s in skills:
            skills_by_slug[str(s.get("slug"))] = s
            nm = str(s.get("name") or "").strip().lower()
            if nm:
                skills_by_slug.setdefault(nm, s)

    # GenImage Router: com o filtro ativo + modelo configurado, o modelo ganha a
    # tool `generate_image` (independe da SIFT — como o view_skill).
    genimage_on = bool(genimage and genimage.get("model"))
    if genimage_on:
        tools = list(tools) + [_generate_image_tool()]

    # Subagentes: com a permissão ligada + um time resolvido, o modelo (orquestrador)
    # ganha a tool `delegate` p/ acionar operários (cada um um ModelConfig próprio).
    subagents = subagents or []
    subagents_by_key = {str(a.get("key")): a for a in subagents}
    subagents_on = bool(subagents and run_subagent is not None)
    if subagents_on:
        tools = list(tools) + [_delegate_tool(subagents)]
    delegations_used = 0

    static_system = _build_static_system(chat_system_prompt, sift_prompt)
    skills_block = _skills_block(skills)
    if skills_block:
        static_system = (static_system + "\n\n" + skills_block).strip()
    # reforço injetado por um Guarda de saída (retry): instrução extra no fim do
    # system prompt, onde tem mais peso. Ver run_turn_guarded.
    if extra_system:
        static_system = (static_system + "\n\n" + extra_system).strip()
    mem_block = _memory_block(memories)
    time_note = _temporal_note(user_tz, user_tz_offset)
    messages: list[dict[str, Any]] = [_system_message(static_system, mem_block, model, time_note)]
    # capacidade "Contexto do Chat": quando desligada, o modelo NÃO recebe o
    # histórico (turno stateless — só system + mensagem atual).
    if use_context:
        messages.extend(history)

    # Anexos: imagens (visão nativa OU Vision Router) e arquivos de texto.
    attachments = attachments or []
    images = [a for a in attachments if a.get("type") == "image" and a.get("url")]
    files = [a for a in attachments if a.get("type") == "file" and a.get("text")]
    file_blocks = "\n\n".join(
        f"[Arquivo anexado: {a.get('name') or 'arquivo'}]\n{a['text']}" for a in files
    )
    base_text = user_text
    if file_blocks:
        base_text = (base_text + "\n\n" + file_blocks).strip() if base_text else file_blocks

    # precedência do tratamento de imagens (modelo sem visão nativa):
    #  - motor "tesseract" (ou "vision" sem router configurado) → OCR local
    #  - motor "vision" com Vision Router → o modelo de visão descreve/transcreve
    use_tesseract = bool(images and ocr and (ocr_engine == "tesseract" or not vision_router_model))
    attach_chars = len(file_blocks)
    if images and vision:
        # visão nativa: manda as imagens como partes image_url (multipart OpenAI)
        parts: list[dict[str, Any]] = []
        if base_text:
            parts.append({"type": "text", "text": base_text})
        for a in images:
            parts.append({"type": "image_url", "image_url": {"url": a["url"]}})
        messages.append({"role": "user", "content": parts})
    elif images and vision_router_model and not use_tesseract:
        # Vision Router: um modelo com visão descreve/transcreve as imagens em texto
        yield {"type": "vision_router", "model": vision_router_model, "count": len(images)}
        try:
            desc = await _describe_images(api_key, vision_router_model, images)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vision Router falhou (%s); seguindo sem descrição", exc)
            desc = ""
        note = (
            f"[O usuário enviou {len(images)} imagem(ns). Um modelo de visão as descreveu:\n{desc}]"
            if desc
            else f"[O usuário enviou {len(images)} imagem(ns), mas não foi possível descrevê-las.]"
        )
        attach_chars += len(note)
        combined = (base_text + "\n\n" + note).strip() if base_text else note
        messages.append({"role": "user", "content": combined})
    elif use_tesseract:
        # OCR local (tesseract): lê o texto das imagens quando não há visão/router
        yield {"type": "ocr", "count": len(images)}
        texts: list[str] = []
        for a in images:
            raw = _decode_data_url(a.get("url"))
            if not raw:
                continue
            try:
                t = await run_in_threadpool(extraction.ocr_image_bytes, raw, ocr_lang)
            except extraction.ExtractionError:
                t = ""
            if t.strip():
                texts.append(t.strip())
        note = (
            "[Texto extraído por OCR das imagens enviadas:\n" + "\n---\n".join(texts) + "]"
            if texts
            else "[O usuário enviou imagens, mas o OCR não encontrou texto legível.]"
        )
        attach_chars += len(note)
        combined = (base_text + "\n\n" + note).strip() if base_text else note
        messages.append({"role": "user", "content": combined})
    elif images:
        note = "[O usuário enviou imagens, mas este modelo não tem Visão, Vision Router nem OCR configurado.]"
        attach_chars += len(note)
        combined = (base_text + "\n\n" + note).strip() if base_text else note
        messages.append({"role": "user", "content": combined})
    else:
        messages.append({"role": "user", "content": base_text})

    # Pesos (em caracteres) de cada origem do prompt, p/ atribuir os tokens de
    # ENTRADA por categoria. O total de prompt_tokens vem real do provedor; a
    # divisão é proporcional ao tamanho de cada bloco (estimativa honesta).
    # Separamos os "custos invisíveis": o CONTEXTO (histórico reenviado a cada
    # turno) e os RESULTADOS de ferramentas (injetados no loop) — que crescem
    # sem o usuário perceber — do input real digitado no promptbox.
    #   user          = mensagem atual (promptbox)
    #   context       = histórico do chat reenviado neste turno
    #   system        = prompt do sistema do chat
    #   memory        = memórias recuperadas do mem0
    #   tools         = system_prompt do SIFT + schemas das tools (overhead fixo)
    #   tool_results  = saídas das ferramentas injetadas durante o loop agêntico
    #   file          = anexos (0 por enquanto; categoria reservada)
    input_chars = {
        "user": len(user_text),
        "context": sum(len(str(m.get("content") or "")) for m in history) if use_context else 0,
        "system": len(chat_system_prompt or ""),
        "memory": len(mem_block),
        "tools": len(sift_prompt) + len(skills_block) + (len(json.dumps(tools)) if tools else 0),
        "tool_results": 0,  # preenchido conforme as tools respondem no loop (inclui view_skill)
        "file": attach_chars,  # arquivos anexados + descrição de imagens (Vision Router)
    }

    assistant_text = ""
    # raciocínio ("thinking") de modelos que o expõem via OpenRouter
    reasoning_text = ""
    reasoning_started: float | None = None
    reasoning_seconds = 0.0
    total_usage: dict[str, float] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "cost": 0.0,
    }
    # registro dos usos de ferramenta neste turno (p/ embutir na mensagem)
    tool_events: list[dict[str, Any]] = []
    # imagens GERADAS NATIVAMENTE pelo modelo (capability image_generation, ex.: nano
    # banana). Deduplicadas por conteúdo — o stream pode repetir a mesma imagem.
    # NÃO emitimos "image_gen start" aqui: a capability ligada não significa que ESTA
    # resposta terá imagem (mensagem de texto normal mostraria "Gerando imagem…" à toa).
    stream_modalities = ["image", "text"] if image_output else None
    seen_images: set[str] = set()
    # Retry de resiliência: se a PRIMEIRA tentativa falhar antes de qualquer chunk e
    # havia knobs opcionais no payload (modalities da capability de imagem / reasoning
    # off injetado), refaz UMA vez sem eles — capability ligada num modelo sem suporte
    # (ex.: image_generation num modelo só-texto → 404/400) degrada p/ texto em vez de
    # quebrar TODA mensagem do chat.
    retried_plain = False
    # "cutucada" final: modelos às vezes terminam MUDOS (ou só com um marcador
    # [[research]]) depois de uma tool pesada — o usuário via o card e nenhuma
    # resposta. Uma única volta extra, sem tools, força a redação final.
    nudged = False

    # 3-4. loop de tool calling
    for _ in range(settings.max_tool_iterations):
        tool_buffer: dict[int, dict] = {}
        finish_reason: str | None = None
        usage: dict | None = None
        got_chunk = False

        try:
            async for chunk in openrouter.stream_chat(
                api_key, model, messages, tools=tools, params=params,
                modalities=stream_modalities, base_url=base_url,
            ):
                got_chunk = True
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("reasoning"):
                        now = time.monotonic()
                        if reasoning_started is None:
                            reasoning_started = now
                        reasoning_text += delta["reasoning"]
                        yield {"type": "reasoning", "text": delta["reasoning"]}
                    if delta.get("content"):
                        if reasoning_started is not None:
                            reasoning_seconds += time.monotonic() - reasoning_started
                            reasoning_started = None
                        assistant_text += delta["content"]
                        yield {"type": "token", "text": delta["content"]}
                    if delta.get("tool_calls"):
                        _accumulate_tool_calls(tool_buffer, delta["tool_calls"])
                    # imagem nativa: pode chegar no delta ou na mensagem final do chunk
                    raw_imgs = delta.get("images") or (choice.get("message") or {}).get("images") or []
                    for im in raw_imgs:
                        url = (im.get("image_url") or {}).get("url") or im.get("url") or ""
                        if not url or not url.startswith("data:") or url in seen_images:
                            continue
                        seen_images.add(url)
                        try:
                            data, mime = image_gen._decode_data_url(url)
                            img_id = await _save_generated_image(user_id, chat_id, mime, data, user_text, model)
                            art = {"kind": "image", "url": image_gen.sign_image_url(img_id), "prompt": user_text}
                            yield {"type": "tool_result", "name": "image", "result": art}
                            tool_events.append({"kind": "result", "name": "image", "data": art})
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Falha ao guardar imagem nativa: %s", exc)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
        except Exception as exc:  # noqa: BLE001
            optional_knobs = bool(stream_modalities) or auto_reasoning_off
            if not got_chunk and not retried_plain and optional_knobs:
                logger.warning(
                    "Stream falhou antes do 1º chunk (%s); repetindo sem knobs opcionais "
                    "(modalities=%s, reasoning_off_injetado=%s)",
                    exc, stream_modalities, auto_reasoning_off,
                )
                retried_plain = True
                stream_modalities = None
                if auto_reasoning_off:
                    params.pop("reasoning", None)
                    auto_reasoning_off = False
                continue
            logger.exception("Erro no streaming do OpenRouter")
            yield {"type": "error", "message": str(exc)}
            return

        if usage:
            _merge_usage(total_usage, usage)
            yield {"type": "usage", "usage": usage}

        if finish_reason != "tool_calls" or not tool_buffer:
            if tool_events and not nudged and _MARKER_ONLY_RE.match(assistant_text or ""):
                nudged = True
                if assistant_text.strip():
                    messages.append({"role": "assistant", "content": assistant_text})
                messages.append({
                    "role": "user",
                    "content": (
                        "Now write your final answer to the user's request in plain text, "
                        "based on the tool results above. Do not call any tools."
                    ),
                })
                tools = None
                continue
            break

        # registra a mensagem do assistant com os tool_calls e executa cada um
        tool_calls = [tool_buffer[i] for i in sorted(tool_buffer)]
        messages.append(
            {"role": "assistant", "content": assistant_text or None, "tool_calls": tool_calls}
        )

        # modo PARALELO: quando o modelo delega a vários operários numa tacada só,
        # roda-os concorrentemente (respeitando o teto de chamadas do turno).
        delegate_pre: dict[str, dict] = {}
        if subagents_on and subagent_mode == "parallel":
            dcalls = [tc for tc in tool_calls if tc["function"]["name"] == "delegate"]
            if len(dcalls) > 1:
                picked: list[tuple[str, str, str]] = []
                for tc in dcalls:
                    if delegations_used >= subagent_max_calls:
                        break
                    try:
                        a = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        a = {}
                    key = str(a.get("agent") or "")
                    spec = subagents_by_key.get(key)
                    if spec is None:
                        continue
                    task = str(a.get("task") or "")
                    yield {"type": "subagent", "status": "start", "agent": spec.get("name", key), "task": task[:200], "parallel": True, "ctx": subagent_pass_context, "mem": subagent_worker_memory}
                    delegations_used += 1
                    picked.append((tc["id"], key, task))
                if picked:
                    results = await asyncio.gather(
                        *[run_subagent(k, t) for (_i, k, t) in picked], return_exceptions=True
                    )
                    for (tcid, _k, _t), res in zip(picked, results):
                        delegate_pre[tcid] = {"error": str(res)} if isinstance(res, Exception) else res

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call", "name": name, "arguments": args}
            tool_events.append({"kind": "call", "name": name, "data": args})

            if name == "view_skill":
                slug = str(args.get("slug") or args.get("name") or "").strip().lower()
                sk = skills_by_slug.get(slug)
                if sk is None:
                    known = ", ".join(sorted({s["slug"] for s in skills})) or "(nenhuma)"
                    result: Any = {"error": f"skill '{slug}' não encontrada. Disponíveis: {known}"}
                else:
                    result = {
                        "slug": sk["slug"],
                        "name": sk["name"],
                        "content": sk.get("content") or "",
                    }
            elif name == "generate_image":
                # GenImage Router: gera a imagem, guarda os bytes e devolve uma URL
                # assinada (pequena) — o base64 NUNCA vai ao contexto do modelo.
                prompt = str(args.get("prompt") or "").strip()
                if not genimage_on:
                    result = {"error": "GenImage Router não está ativo neste modelo"}
                elif not prompt:
                    result = {"error": "`prompt` é obrigatório"}
                else:
                    yield {"type": "image_gen", "status": "start", "prompt": prompt[:120]}
                    try:
                        keys = {"openrouter": api_key, "imagegen": (genimage or {}).get("imagegen_key")}
                        img_bytes, mime = await image_gen.generate(
                            genimage, keys, prompt, str(args.get("size") or "1024x1024")
                        )
                        image_id = await _save_generated_image(
                            user_id, chat_id, mime, img_bytes, prompt, (genimage or {}).get("model", "")
                        )
                        result = {"kind": "image", "url": image_gen.sign_image_url(image_id), "prompt": prompt}
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Falha ao gerar imagem: %s", exc)
                        yield {"type": "image_gen", "status": "error"}
                        result = {"error": f"não foi possível gerar a imagem: {exc}"}
            elif name == "delegate":
                if not subagents_on:
                    result = {"error": "subagentes não habilitados neste modelo"}
                elif tc["id"] in delegate_pre:  # já rodou em paralelo
                    result = delegate_pre[tc["id"]]
                    yield {"type": "subagent", "status": "done", "agent": (result.get("agent") if isinstance(result, dict) else None) or str(args.get("agent") or "")}
                elif delegations_used >= subagent_max_calls:
                    result = {"error": f"limite de {subagent_max_calls} delegações por turno atingido"}
                else:
                    key = str(args.get("agent") or "")
                    task = str(args.get("task") or "")
                    spec = subagents_by_key.get(key)
                    if spec is None:
                        result = {"error": f"subagente '{key}' não autorizado"}
                    else:
                        yield {"type": "subagent", "status": "start", "agent": spec.get("name", key), "task": task[:200], "ctx": subagent_pass_context, "mem": subagent_worker_memory}
                        delegations_used += 1
                        try:
                            result = await run_subagent(key, task)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Subagente falhou: %s", exc)
                            result = {"error": f"o subagente falhou: {exc}"}
                        yield {"type": "subagent", "status": "done", "agent": (result.get("agent") if isinstance(result, dict) else None) or spec.get("name", key)}
            elif sift is None:
                result = {"error": "ferramentas indisponíveis"}
            elif name == "run_code" and not code_mode:
                # run_code não foi anunciado a este modelo; não executa código
                result = {"error": "run_code não está habilitado para este modelo"}
            else:
                result = await run_in_threadpool(sift.dispatch, name, args)
                # Path errado/fora do escopo (modelo chutou, ex.: 'web.read' em vez
                # de 'web.page.read'): enriquece o erro com o caminho de recuperação,
                # senão modelos fracos DESISTEM e dizem que a ferramenta não existe.
                if isinstance(result, str) and (
                    "not allowed in this scope" in result or "unknown tool" in result.lower()
                ):
                    try:
                        _r = json.loads(result)
                        if isinstance(_r, dict) and _r.get("error"):
                            _r["hint"] = (
                                "This tool path does not exist here. Call search_tools "
                                "with a short query to discover the CORRECT path, then "
                                "retry execute_tool — do not tell the user the tool is unavailable."
                            )
                            result = json.dumps(_r, ensure_ascii=False)
                    except (json.JSONDecodeError, ValueError):
                        pass
            # SIFT v0.4 retorna strings (JSON ou texto p/ search_tools); não
            # re-serializar para não duplo-codificar o conteúdo enviado ao modelo.
            if isinstance(result, str):
                content = result
                try:
                    event_result: Any = json.loads(result)
                except (json.JSONDecodeError, ValueError):
                    event_result = result
            else:
                content = json.dumps(result, ensure_ascii=False, default=str)
                event_result = result
            # artefatos que o FRONT renderiza mas que o modelo não deve reproduzir:
            # o modelo recebe só uma nota enxuta (event_result cobre str-JSON e dict).
            # imagem: não repetir a URL interna nem o base64.
            if isinstance(event_result, dict) and event_result.get("kind") == "image":
                content = json.dumps({"ok": True, "note": "Image generated and shown to the user."})
            # rascunho de e-mail: um composer editável foi mostrado ao usuário — o
            # modelo NÃO deve afirmar que já enviou (o usuário revisa e envia).
            if isinstance(event_result, dict) and event_result.get("kind") == "email_draft":
                content = json.dumps({
                    "ok": True,
                    "note": "An editable email draft was shown to the user to review and send. "
                            "Do NOT claim the email was sent; the user will send it from the composer.",
                })
            yield {"type": "tool_result", "name": name, "result": event_result}
            tool_events.append({"kind": "result", "name": name, "data": event_result})

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": content,
                }
            )
            input_chars["tool_results"] += len(content)  # custo invisível: saída da tool volta como entrada

        assistant_text = ""  # reinicia p/ a próxima volta (resposta final)

    # 5. memória pós-turno (só em chats persistentes; escopo escolhido pelo chat).
    # Dispara em BACKGROUND: não deve atrasar o `done`/conclusão visível na UI.
    if assistant_text and chat_id and mem_write and mem_write != "off":
        _spawn_memory_write(api_key, user_text, assistant_text, user_id, chat_id, agent_id, mem_write, mem_review)

    # fecha a duração caso o stream tenha terminado ainda "pensando"
    if reasoning_started is not None:
        reasoning_seconds += time.monotonic() - reasoning_started

    # ENTRADA por categoria: distribui o total real de prompt_tokens
    # proporcionalmente ao tamanho (chars) de cada bloco.
    prompt_total = int(total_usage.get("prompt_tokens", 0) or 0)
    weight_total = sum(input_chars.values())
    if prompt_total and weight_total:
        input_breakdown = {
            k: round(prompt_total * v / weight_total) for k, v in input_chars.items()
        }
    else:
        input_breakdown = {k: 0 for k in input_chars}

    # SAÍDA por categoria: saída "visível" vs raciocínio (thinking)
    completion_total = int(total_usage.get("completion_tokens", 0) or 0)
    reasoning_tokens = int(total_usage.get("reasoning_tokens", 0) or 0)
    # fallback: se o provedor não separou, estima o thinking pelo texto capturado
    if not reasoning_tokens and reasoning_text and completion_total:
        est = round(len(reasoning_text) / 4)
        reasoning_tokens = min(est, completion_total)
    output_breakdown = {
        "output": max(0, completion_total - reasoning_tokens),
        "thinking": reasoning_tokens,
    }
    total_usage["input_breakdown"] = input_breakdown
    total_usage["output_breakdown"] = output_breakdown

    has_usage = total_usage["total_tokens"] > 0 or total_usage["cost"] > 0
    yield {
        "type": "done",
        "content": assistant_text,
        "usage": total_usage if has_usage else None,
        "reasoning": (
            {"text": reasoning_text, "seconds": round(reasoning_seconds, 1)}
            if reasoning_text
            else None
        ),
        "tool_events": tool_events or None,
        "memories": mem_items or None,
    }


# --------------------------------------------------------------------------- #
# Guardas de saída (Output Guards) — filtros que INSPECIONAM a resposta e, se ela
# casa com uma condição (recusa / vazia / regex), REAGEM: reforçam o system prompt
# e refazem, ou caem para um modelo de fallback. Vários guardas por modelo, cada um
# com nome, detecção, reação e teto de tentativas próprios. Ver routes._resolve_guards.
# --------------------------------------------------------------------------- #

# frases típicas de recusa (PT + EN), procuradas no INÍCIO da resposta p/ reduzir
# falso-positivo (uma recusa costuma vir logo de cara).
_GUARD_REFUSAL = (
    "não posso", "nao posso", "não vou", "nao vou", "não poderei", "nao poderei",
    "desculpe, mas", "desculpe mas", "sinto muito, mas", "sinto muito mas",
    "infelizmente não", "infelizmente nao", "como uma ia", "como um modelo de linguagem",
    "não sou capaz", "nao sou capaz", "não é apropriado", "nao e apropriado",
    "não posso ajudar", "nao posso ajudar", "não posso atender", "nao posso atender",
    "vai contra minhas", "não posso continuar", "nao posso continuar",
    "i can't", "i cannot", "i can not", "i'm sorry, but", "i am sorry, but",
    "i'm unable", "i am unable", "as an ai", "i won't", "i will not",
    "i'm not able", "i am not able", "against my", "not appropriate",
    "i must decline", "i'm not comfortable", "cannot assist", "can't assist",
)

# teto global de tentativas por turno (defesa contra loop/custo), independente da
# soma dos max_retries dos guardas.
_GUARD_HARD_CAP = 4


def _guard_triggered(guard: dict, text: str, done: dict | None) -> bool:
    """A resposta casa com a condição deste guarda?"""
    body = text or ""
    detect = guard.get("detect") or "refusal"
    if detect == "empty":
        stripped = body.strip()
        min_len = int(guard.get("min_len") or 0)
        if min_len > 0:
            return len(stripped) < min_len
        # sem texto E sem artefato de ferramenta (ex.: imagem) → resposta "vazia"
        return not stripped and not (done or {}).get("tool_events")
    if detect == "regex":
        pat = (guard.get("pattern") or "").strip()
        if not pat:
            return False
        try:
            return re.search(pat, body, re.IGNORECASE | re.DOTALL) is not None
        except re.error:
            return False
    # refusal (padrão): procura padrões de recusa no começo da resposta
    head = body[:600].lower()
    return any(p in head for p in _GUARD_REFUSAL)


_JUDGE_SYSTEM = (
    "Você é um classificador de saída, imparcial e objetivo. Recebe um CRITÉRIO "
    "definido pelo usuário e a RESPOSTA de um assistente. Decida se a resposta se "
    "enquadra no critério (ou seja, se o guarda deve AGIR). Responda com uma única "
    "palavra: 'SIM' se enquadra, 'NÃO' se não. Não explique."
)


async def _judge_triggered(guard: dict, text: str, user_text: str) -> bool:
    """Detecção por 'juiz' LLM: um modelo barato avalia a resposta contra um
    critério livre escrito pelo usuário. Totalmente customizável. Falha do juiz
    NÃO bloqueia o turno (retorna False)."""
    model = (guard.get("judge_model") or "").strip()
    key = guard.get("_judge_api_key")
    criterion = (guard.get("criterion") or "").strip()
    if not model or not key or not criterion:
        return False
    user = (
        f"CRITÉRIO (quando o guarda deve agir):\n{criterion}\n\n"
        f"MENSAGEM DO USUÁRIO:\n{user_text or '(vazia)'}\n\n"
        f"RESPOSTA DO ASSISTENTE:\n{text or '(vazia)'}\n\n"
        "A resposta se enquadra no critério? Responda apenas SIM ou NÃO."
    )
    try:
        out = await openrouter.complete(
            key, model,
            [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}],
            base_url=guard.get("_judge_base_url"),
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001 - juiz é best-effort
        logger.warning("Juiz do guarda de saída falhou (%s); não aciona", exc)
        return False
    ans = (out or "").strip().lower()
    return ans.startswith("sim") or ans.startswith("yes")


def _merge_done_usage(acc: dict | None, u: dict | None) -> dict | None:
    """Soma o usage de uma tentativa ao acumulado (custo real das re-tentativas)."""
    if not u:
        return acc
    if acc is None:
        return dict(u)
    for k in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"):
        acc[k] = int(acc.get(k, 0) or 0) + int(u.get(k, 0) or 0)
    acc["cost"] = float(acc.get("cost", 0.0) or 0.0) + float(u.get("cost", 0.0) or 0.0)
    for grp in ("input_breakdown", "output_breakdown"):
        merged = dict(acc.get(grp) or {})
        for k, v in (u.get(grp) or {}).items():
            merged[k] = int(merged.get(k, 0) or 0) + int(v or 0)
        if merged:
            acc[grp] = merged
    return acc


async def run_turn_guarded(
    *, guards: list[dict] | None = None, **turn_kwargs: Any
) -> AsyncGenerator[dict[str, Any], None]:
    """Envolve ``run_turn`` com os Guardas de saída. Sem guardas, é um passthrough.

    A cada tentativa: encaminha os eventos ao vivo, intercepta o ``done``/``error``,
    checa os guardas na resposta e — se algum casa e ainda há orçamento — aplica a
    reação (reforçar o system prompt OU trocar p/ o modelo de fallback), emite
    ``guard``/``guard_reset`` (o front limpa o buffer) e refaz. No fim, emite um
    único ``done`` com o usage somado das tentativas."""
    guards = [g for g in (guards or []) if g.get("enabled", True)]
    if not guards:
        async for ev in run_turn(**turn_kwargs):
            yield ev
        return

    remaining = {g["id"]: max(0, int(g.get("max_retries") or 1)) for g in guards}
    base_model = turn_kwargs.get("model")
    base_key = turn_kwargs.get("api_key")
    base_base = turn_kwargs.get("base_url")
    cur_model, cur_key, cur_base = base_model, base_key, base_base
    extra_system: str | None = None
    merged_usage: dict | None = None
    attempt = 0

    while True:
        attempt += 1
        kw = {
            **turn_kwargs,
            "model": cur_model, "api_key": cur_key, "base_url": cur_base,
            "extra_system": extra_system,
        }
        final_done: dict | None = None
        last_error: dict | None = None
        async for ev in run_turn(**kw):
            t = ev.get("type")
            if t == "done":
                final_done = ev
            elif t == "error":
                last_error = ev  # segura: só re-emite se desistir
            elif t == "usage":
                pass  # somado via done abaixo; não reencaminha (evita duplicar)
            else:
                yield ev

        text = (final_done or {}).get("content") or ""
        merged_usage = _merge_done_usage(merged_usage, (final_done or {}).get("usage"))

        # escolhe o 1º guarda (com orçamento) que casa com a resposta
        hit = None
        if attempt < _GUARD_HARD_CAP:
            for g in guards:
                if remaining.get(g["id"], 0) <= 0:
                    continue
                if g.get("detect") == "judge":
                    matched = await _judge_triggered(g, text, turn_kwargs.get("user_text", ""))
                else:
                    matched = _guard_triggered(g, text, final_done)
                if matched:
                    hit = g
                    break

        if hit is not None:
            remaining[hit["id"]] -= 1
            action = hit.get("action") or "reinforce"
            if action == "fallback_model" and (hit.get("fallback_model") or "").strip():
                cur_model = hit["fallback_model"].strip()
                cur_key = hit.get("_api_key") or base_key
                cur_base = hit.get("_base_url")
            else:  # reinforce
                inj = (hit.get("inject_text") or "").strip()
                if inj:
                    extra_system = (extra_system + "\n\n" + inj).strip() if extra_system else inj
            yield {
                "type": "guard",
                "name": hit.get("name") or "Guarda de saída",
                "action": "fallback_model" if action == "fallback_model" else "reinforce",
                "attempt": attempt,
            }
            yield {"type": "guard_reset"}  # o front descarta a tentativa anterior
            continue

        # aceito (ou orçamento esgotado): emite o resultado final
        if final_done is not None:
            if merged_usage is not None:
                final_done = {**final_done, "usage": merged_usage}
            yield final_done
        elif last_error is not None:
            yield last_error
        return
