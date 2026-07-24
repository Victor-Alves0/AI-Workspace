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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi.concurrency import run_in_threadpool

from .. import extraction
from ..config import get_settings
from ..db import SessionLocal
from ..knowledge import brain as brain_service
from ..knowledge import retrieval as kb_retrieval
from ..knowledge.links import sign_doc_url
from ..memory import mem0_service
from ..models import GeneratedImage
from ..providers import image_gen, openrouter
from ..tools import sift_service, toolctx
from . import curator

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
    *, project_id: str | None = None,
) -> None:
    """Grava a memória pós-turno no escopo escolhido (global/model/chat/project), SEM
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
                    project_id=project_id,
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

# Modo Código: tools longas promovidas a specs de 1ª classe ao lado do run_code
# ficam "vitrine" — sem enquadramento, o modelo as usa como caminho padrão de busca
# (ex.: research__deep__run disparada sem o usuário pedir pesquisa profunda).
_PROMOTED_TOOLS_NOTE = (
    "IMPORTANT — the directly-exposed tool(s) {names} are LONG-RUNNING specialists, "
    "surfaced beside run_code only because they cannot run inside its sandbox. They are "
    "NOT the default path for looking things up. For ordinary lookups (news, prices, "
    "quotes, quick facts, reading a page) use run_code with web.search.query / "
    "web.page.read. Call {names} ONLY in the cases its own description allows — e.g. "
    "deep research the user EXPLICITLY requested."
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


# Ponte entre os DOIS mundos de ferramentas do turno: as do índice SIFT (descobertas
# via search_tools) e as NATIVAS, injetadas direto no array de tools. Sem esta ponte o
# modelo trata search_tools como o único caminho e nega capacidades que TEM em mãos.
_NATIVE_TOOLS_NOTE = (
    "Besides the SIFT meta-tools, these tools are ALREADY in your tool list and are called "
    "DIRECTLY by name: {names}. `search_tools` does NOT index them, so a search that returns "
    "nothing says NOTHING about them — never conclude you lack a capability they cover. In "
    "particular, if the user asks for a document, photo, image or video THEY own, call "
    "`search_knowledge` (when listed) before saying you don't have it."
)


def _native_tools_note(names: list[str]) -> str:
    return _NATIVE_TOOLS_NOTE.format(names=", ".join(f"`{n}`" for n in names))


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

# referência "#": abaixo deste tamanho (chars ≈ 6k tokens) o arquivo entra INTEIRO
# no contexto; acima, cai p/ os trechos mais relevantes (retrieval no próprio doc).
_REF_FULLTEXT_LIMIT = 24000


def _build_static_system(chat_system_prompt: str | None, sift_prompt: str) -> str:
    """Parte ESTÁVEL do system (prompt do chat + SIFT) — o prefixo cacheável."""
    parts: list[str] = []
    if chat_system_prompt:
        parts.append(chat_system_prompt.strip())
    if sift_prompt:
        parts.append(sift_prompt)
    return "\n\n".join(p for p in parts if p)


# Prompt lido pelo MODELO => inglês (padrão do projeto: a UI fica no idioma do app,
# o que a IA lê fica em inglês). A DESCRIÇÃO sai no idioma da conversa porque ela é
# repassada a um assistente que responde ao usuário.
_VISION_DESCRIBE_PROMPT = (
    "Describe the following images in detail so that another assistant, which CANNOT see "
    "them, understands everything relevant: text (transcribe it verbatim), objects, people, "
    "charts, numbers, colors and any important detail. Be objective and complete. Describe "
    "only what is actually visible — never guess or infer what is not there. Write the "
    "description in the same language as the conversation."
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


# formato exigido pela API de áudio (input_audio.format) a partir do mime do data URL
_AUDIO_FMT = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/opus": "ogg",
    "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
}


def _audio_url_parts(url: str | None) -> tuple[str, str] | None:
    """(mime, base64) de um data URL de áudio; None se malformado."""
    if not url or not isinstance(url, str) or "," not in url:
        return None
    head, b64 = url.split(",", 1)
    mime = head.removeprefix("data:").split(";", 1)[0].strip().lower() or "audio/mpeg"
    return mime, b64


async def _transcribe_audios(
    cfg: dict[str, Any], audios: list[dict[str, Any]], api_key: str
) -> str:
    """Audio Router: transcreve os áudios anexados p/ o modelo 'ouvir' em texto.

    Dois motores (espelha o Vision Router):
      - "stt":   provedor de voz global (Whisper, /audio/transcriptions) — cfg traz
                 base_url/api_key/model resolvidos pela rota (aqui não há db).
      - "model": um modelo multimodal de áudio via OpenRouter (partes input_audio).
    Falha em um áudio não derruba os demais; devolve as transcrições unidas."""
    async def _try_one(c: dict[str, Any], mime: str, b64: str, name: str) -> str:
        if c.get("engine") == "model":
            fmt = _AUDIO_FMT.get(mime, "mp3")
            return await openrouter.complete(api_key, c["model"], [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this audio verbatim. Output ONLY the transcription, in the audio's language."},
                    {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                ],
            }])
        # stt (Whisper OpenAI-compat)
        raw = base64.b64decode(b64, validate=False)
        ext = _AUDIO_FMT.get(mime, "mp3")
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{c['base_url']}/audio/transcriptions",
                headers={"Authorization": f"Bearer {c['api_key']}"},
                files={"file": (name or f"audio.{ext}", raw, mime)},
                data={"model": c.get("model") or "whisper-1"},
            )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()

    # cadeia de motores: o principal + os fallbacks resolvidos pela rota (ex.: a
    # conexão de voz local não faz STT → 404 → cai no modelo multimodal). Antes um
    # único motor falho deixava o canal SURDO em silêncio.
    chain: list[dict[str, Any]] = [cfg, *cfg.get("fallbacks", [])]
    texts: list[str] = []
    for a in audios:
        parts = _audio_url_parts(a.get("url"))
        if parts is None:
            continue
        mime, b64 = parts
        text = ""
        for c in chain:
            try:
                text = await _try_one(c, mime, b64, a.get("name") or "")
            except Exception as exc:  # noqa: BLE001 - tenta o próximo motor
                logger.warning("Audio Router: motor %s falhou (%s)", c.get("engine"), exc)
                text = ""
            if text:
                break
        if text:
            texts.append(text)
    return "\n---\n".join(texts)


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


def _search_knowledge_tool() -> dict[str, Any]:
    """Tool injetada no modo 'ferramenta' da Base de Conhecimento: o modelo busca
    trechos dos documentos do usuário quando julga precisar (premissa SIFT)."""
    return {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Search the user's knowledge base (their uploaded documents AND images/photos/videos) "
                "for items relevant to a question. Use whenever the answer may depend on the "
                "user's own files — including when they ask you to show/send a photo, image or video "
                "stored there. Returns numbered passages (cite the source used with [n]); image and "
                "video results include ready-to-paste markdown that DISPLAYS/PLAYS the media in the chat. "
                "Call it again with different words (or a bigger `limit`) if the first search misses — "
                "do not conclude the user has no such file after a single query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "what to look up, in the document's language"},
                    "limit": {
                        "type": "integer",
                        "description": (
                            "how many passages/files to retrieve (1-20). Raise it to browse "
                            "what exists (e.g. listing the user's photos); lower it to save context."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    }


def _brain_tool(write: bool) -> dict[str, Any]:
    """Tool injetada quando há cérebros (second brain) acoplados: a IA lê/busca —
    e, com `write` ligado, cria/atualiza — notas markdown [[interligadas]]."""
    actions = ["list", "search", "read"] + (["write"] if write else [])
    desc = (
        "Access the user's second brain: interlinked markdown notes with durable, "
        "structured knowledge. Actions: 'list' note titles; 'search' notes by meaning; "
        "'read' a note by title (returns content and its links/backlinks)"
    )
    if write:
        desc += (
            "; 'write' to create or update a note (full markdown body; link related "
            "notes with [[Note Title]]). Write when the conversation produces reusable "
            "knowledge: concepts, decisions, research syntheses, project notes. Prefer "
            "updating an existing note over duplicating it"
        )
    desc += (
        ". Do NOT store short personal facts about the user here — the memory system "
        "captures those automatically."
    )
    return {
        "type": "function",
        "function": {
            "name": "brain",
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": actions},
                    "query": {"type": "string", "description": "search: what to look for"},
                    "title": {"type": "string", "description": "read/write: the note title"},
                    "content": {
                        "type": "string",
                        "description": "write: full markdown body, with [[wikilinks]] to related notes",
                    },
                    "mode": {
                        "type": "string", "enum": ["replace", "append"],
                        "description": "write: replace the note (default) or append to it",
                    },
                    "brain": {
                        "type": "string",
                        "description": "brain name, when more than one is attached",
                    },
                },
                "required": ["action"],
            },
        },
    }


def _propose_skill_tool() -> dict[str, Any]:
    """Tool do /learn: o modelo DESTILA uma skill do trabalho do chat e a propõe.
    Proposal-only — o card editável na UI é quem salva (POST /skills), nunca o modelo."""
    return {
        "type": "function",
        "function": {
            "name": "propose_skill",
            "description": (
                "Propose a new reusable skill distilled from work completed in this "
                "conversation. Use when the user asks to learn/save a procedure, or — at "
                "most once per conversation — right after finishing a multi-step task whose "
                "procedure is genuinely reusable. The user reviews the proposal in an "
                "editable card; the skill is NEVER saved automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "short human-readable skill name"},
                    "slug": {
                        "type": "string",
                        "description": "lowercase identifier (letters/digits/underscore), e.g. code_review",
                    },
                    "description": {
                        "type": "string",
                        "description": "WHEN to use this skill, 1-3 sentences (always visible to the model)",
                    },
                    "content": {
                        "type": "string",
                        "description": "HOW to do it: complete step-by-step instructions in markdown",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "description", "content"],
            },
        },
    }


def _knowledge_block_and_sources(results: list[dict]) -> tuple[str, list[dict[str, str]]]:
    """Dos trechos recuperados, monta (a) o bloco numerado por FONTE (documento) p/ o
    modelo e (b) a lista de fontes [{title, url}] (uma por documento, ordem de 1ª
    aparição) — a numeração [n] bate entre os dois, e a UI (`SourcesBar`/
    `linkifyCitations`) transforma [n] em link p/ o documento original assinado.

    IMAGENS da base: o "trecho" é só o nome indexado — o que interessa é EXIBIR.
    A linha entrega ao modelo o markdown pronto (`![nome](url assinada)`) p/ colar
    na resposta; o chat renderiza (o front resolve a URL no host da API)."""
    sources: list[dict[str, str]] = []
    idx: dict[str, int] = {}
    lines: list[str] = []
    for r in results:
        did = r["doc_id"]
        if did not in idx:
            idx[did] = len(sources) + 1
            sources.append({"title": r.get("filename") or "documento", "url": sign_doc_url(did)})
        if (r.get("mime") or "").startswith("image/"):
            name = r.get("filename") or "imagem"
            lines.append(
                f"[{idx[did]}] IMAGE — {r.get('text') or name}. "
                f"To SHOW this image in your reply, paste exactly: ![{name}]({sources[idx[did]-1]['url']})"
            )
        elif (r.get("mime") or "").startswith("video/"):
            # o front renderiza <video> quando o `alt` do markdown termina com uma
            # extensão de vídeo — por isso mantenha o nome do arquivo (com extensão).
            name = r.get("filename") or "video.mp4"
            lines.append(
                f"[{idx[did]}] VIDEO — {r.get('text') or name}. "
                f"To SHOW/PLAY this video in your reply, paste exactly: ![{name}]({sources[idx[did]-1]['url']})"
            )
        else:
            lines.append(f"[{idx[did]}] {r.get('text') or ''}")
    return "\n\n".join(lines), sources


# Imagens da Base de Conhecimento que o modelo "cola" na resposta vêm com um token
# assinado longo (~150 chars). Modelos às vezes ADULTERAM/TRUNCAM esse token ao
# reproduzi-lo → o /raw responde 403 e a imagem renderiza QUEBRADA no chat (a borda
# + o alt/nome do arquivo). Reassinamos server-side toda URL /knowledge/docs/<uuid>/raw
# na resposta final: o doc_id (mais curto/robusto) é o que importa; o token é gerado
# fresco aqui, então a imagem sempre carrega — mesmo que o modelo tenha estragado o dele.
_KB_IMG_RE = re.compile(r"/knowledge/docs/([0-9a-fA-F-]{36})/raw(?:\?t=[^)\s\"'<>]*)?")


def _resign_kb_images(text: str) -> str:
    if not text or "/knowledge/docs/" not in text:
        return text
    return _KB_IMG_RE.sub(lambda m: sign_doc_url(m.group(1)), text)


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


def _brain_block(names: list[str], write: bool) -> str:
    """Bloco (estável, cacheável) do second brain: diz que o cérebro existe, como
    usar e a FRONTEIRA com o mem0 (fatos curtos do usuário = memória automática)."""
    if not names:
        return ""
    listed = ", ".join(f"`{n}`" for n in names if n) or "`brain`"
    lines = [
        "## Second brain",
        f"You maintain the user's second brain ({listed}): interlinked markdown notes "
        "holding deliberate, durable knowledge. Use the `brain` tool to list, search and "
        "read notes whenever past notes may inform the answer.",
    ]
    if write:
        lines.append(
            "When this conversation produces reusable knowledge — concepts, decisions, "
            "research syntheses, project notes — write it down (action 'write'), linking "
            "related notes with [[Note Title]]. Prefer atomic notes with descriptive "
            "titles; update existing notes instead of duplicating them."
        )
    lines.append(
        "Do NOT store short personal facts about the user in the brain — the memory "
        "system captures those automatically."
    )
    return "\n".join(lines)


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


def _priority_block(extra_system: str | None) -> str:
    """Instruções de alta prioridade (guardas/canal) com um cabeçalho que deixa
    claro ao modelo que elas prevalecem. Fica no FIM do system (maior peso)."""
    txt = (extra_system or "").strip()
    if not txt:
        return ""
    return (
        "=== INSTRUÇÕES PRIORITÁRIAS ===\n"
        "As diretrizes abaixo têm precedência sobre as anteriores; siga-as à risca.\n"
        f"{txt}"
    )


def _system_message(
    static_system: str, mem_block: str, model: str, time_note: str = "",
    extra_system: str | None = None,
) -> dict[str, Any]:
    """Monta a mensagem `system`. Para provedores de cache explícito, marca a
    parte estável com cache_control e deixa memória + hora + prioridade depois (sem
    cache), para que o prefixo grande e repetido seja de fato reaproveitado entre
    turnos (a hora muda a cada minuto e o reforço do guarda muda a cada tentativa —
    jamais podem entrar no prefixo cacheado). A ordem do tail coloca as instruções
    PRIORITÁRIAS por último = última coisa lida pelo modelo (maior peso)."""
    tail = "\n\n".join([b for b in (time_note, mem_block, _priority_block(extra_system)) if b])
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


# --------------------------------------------------------------------------- #
# Opções do turno — grupos coesos no lugar dos ~40 kwargs soltos de antes.
# `model`/`api_key`/`base_url`/`extra_system`/`extra_breakdown`/`user_text`
# continuam FLAT de propósito: o run_turn_guarded os sobrescreve entre tentativas.
# --------------------------------------------------------------------------- #

@dataclass
class TurnSession:
    """Identidade e ambiente do turno (quem, onde, fuso, autônomo?)."""
    user_id: str
    user_tz: str = ""
    user_tz_offset: int | None = None
    chat_id: str | None = None
    agent_id: str | None = None
    # execução autônoma (automação/canal): tools pulam fases interativas
    background: bool = False
    user_profile: dict[str, Any] | None = None
    # Codespace: projeto vinculado a este chat (habilita code.graph.query/
    # code.files.browse mirando ele). None = sem projeto — as tools avisam.
    codespace_project_id: str | None = None


@dataclass
class MemoryOpts:
    """Memória (mem0): escopos de leitura, destino de escrita, bancos e projeto."""
    read: dict[str, bool] | None = None
    write: str = "off"
    review: bool = False
    banks: list[str] | None = None
    # id do "projeto" (a pasta do chat) p/ o escopo de memória compartilhado
    project: str | None = None


@dataclass
class MediaOpts:
    """Anexos e roteadores de mídia (visão, áudio, OCR, geração de imagem)."""
    attachments: list[dict[str, Any]] | None = None
    vision: bool = False
    vision_router_model: str | None = None
    # Audio Router: {"engine":"stt", base_url, api_key, model} ou {"engine":"model", model}
    audio_router: dict[str, Any] | None = None
    ocr: bool = False
    ocr_engine: str = "tesseract"
    ocr_lang: str = "por+eng"
    genimage: dict[str, Any] | None = None
    image_output: bool = False


@dataclass
class SubagentOpts:
    """Delegação a operários (tool `delegate`)."""
    agents: list[dict[str, Any]] | None = None
    run: Any | None = None  # closure run_subagent(key, task) -> dict
    mode: str = "sequential"
    max_calls: int = 4
    pass_context: bool = False
    worker_memory: bool = False


# --------------------------------------------------------------------------- #
# Fases do turno (extraídas do corpo do run_turn; o loop agêntico fica nele)
# --------------------------------------------------------------------------- #

@dataclass
class _GatheredContext:
    """Estado produzido pela fase de contexto (memória + conhecimento + #refs)."""
    mem_items: list[dict[str, str]] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    knowledge_block: str = ""
    ref_block: str = ""
    auto_knowledge_event: dict[str, Any] | None = None
    ref_knowledge_event: dict[str, Any] | None = None
    kb_bases: list[str] = field(default_factory=list)
    kb_mode: str = "auto"
    kb_k: int = 6
    kb_on: bool = False
    # bases separadas por MODO (override por base; cai no kb_mode como padrão)
    kb_bases_auto: list[str] = field(default_factory=list)
    kb_bases_tool: list[str] = field(default_factory=list)


async def _gather_context(
    g: _GatheredContext,
    *,
    api_key: str,
    user_text: str,
    session: TurnSession,
    memory: MemoryOpts,
    knowledge: dict[str, Any] | None,
    ref_docs: list[dict[str, Any]] | None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Fase 1 — memória (mem0), Base de Conhecimento (modo auto) e arquivos "#".

    Emite os eventos de progresso e deixa os blocos/fontes em `g`."""
    user_id, chat_id, agent_id = session.user_id, session.chat_id, session.agent_id

    # 1. memória — UNIÃO dos escopos ligados em `memory.read` ({global, model, chat}).
    # Leak-safe: só global (compartilhado) + as do modelo atual + as deste chat —
    # nunca de outros chats/modelos. Ver memory/mem0_service.search_for_turn.
    if (memory.read and any(memory.read.values())) or memory.banks:
        g.mem_items = await run_in_threadpool(
            lambda: mem0_service.search_for_turn(
                api_key, user_text, user_id,
                chat_id=chat_id, agent_id=agent_id,
                read=memory.read or {}, banks=memory.banks,
                project_id=memory.project, limit=6,
            )
        )
    g.memories = [m["text"] for m in g.mem_items]
    if g.mem_items:
        yield {"type": "memory", "count": len(g.mem_items), "items": g.mem_items}

    # 1b. Base de Conhecimento (RAG). Duas formas de uso (escolha do usuário):
    #   - "auto": recupera os trechos relevantes JÁ e injeta no system (com citações);
    #   - "tool": expõe `search_knowledge` p/ o modelo buscar sob demanda (no loop).
    kb = knowledge or {}
    g.kb_bases = [str(b) for b in (kb.get("bases") or []) if b]
    g.kb_mode = (kb.get("mode") or "auto").lower()
    g.kb_k = int(kb.get("k") or 6)
    g.kb_on = bool(g.kb_bases)
    # MODO por base (override); sem override, cai no g.kb_mode (padrão do modelo)
    kb_modes = {str(k): str(v).lower() for k, v in (kb.get("modes") or {}).items()}
    g.kb_bases_auto = [b for b in g.kb_bases if (kb_modes.get(b) or g.kb_mode) == "auto"]
    g.kb_bases_tool = [b for b in g.kb_bases if (kb_modes.get(b) or g.kb_mode) == "tool"]
    if g.kb_bases_auto:
        yield {"type": "knowledge", "status": "start", "query": user_text[:120]}
        try:
            auto_kres = await kb_retrieval.search(user_id, g.kb_bases_auto, user_text, g.kb_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("busca na base de conhecimento falhou: %s", exc)
            auto_kres = []
        _blk, auto_sources = _knowledge_block_and_sources(auto_kres)
        if auto_kres:
            g.knowledge_block = (
                "## Base de conhecimento\n"
                "Trechos recuperados dos documentos do usuário (numerados por fonte). Baseie a "
                "resposta neles quando pertinente e CITE a fonte usada com [n]. Se a resposta "
                "não estiver nos trechos, diga que não encontrou na base.\n\n" + _blk
            )
        g.auto_knowledge_event = {
            "kind": "knowledge", "query": user_text[:200],
            "sources": auto_sources, "count": len(auto_kres),
        }
        yield {"type": "tool_result", "name": "knowledge", "result": g.auto_knowledge_event}

    # 1c. Arquivos referenciados com "#" no compositor. HÍBRIDO: doc pequeno entra
    # inteiro; doc grande cai p/ os trechos mais relevantes (retrieval no próprio doc).
    ref_list = ref_docs or []
    if ref_list:
        ref_results: list[dict[str, Any]] = []
        for rd in ref_list:
            text = (rd.get("text") or "").strip()
            if not text:
                continue
            if len(text) <= _REF_FULLTEXT_LIMIT:
                ref_results.append({"doc_id": rd.get("id"), "filename": rd.get("filename"), "text": text})
            else:
                try:
                    hits = await kb_retrieval.search(
                        user_id, [str(rd.get("base_id"))], user_text, 6, doc_ids=[str(rd.get("id"))]
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("busca no doc referenciado falhou: %s", exc)
                    hits = []
                if hits:
                    ref_results.extend(hits)
                else:
                    # sem match/índice: injeta o começo do arquivo (com corte)
                    ref_results.append({
                        "doc_id": rd.get("id"), "filename": rd.get("filename"),
                        "text": text[:_REF_FULLTEXT_LIMIT],
                    })
        if ref_results:
            _rblk, ref_sources = _knowledge_block_and_sources(ref_results)
            g.ref_block = (
                "## Arquivos referenciados\n"
                "O usuário anexou estes arquivos com \"#\" (numerados por fonte). Use-os como "
                "base principal da resposta e CITE a fonte com [n] quando aplicável.\n\n" + _rblk
            )
            g.ref_knowledge_event = {
                "kind": "knowledge", "query": user_text[:200],
                "sources": ref_sources, "count": len(ref_results),
            }
            yield {"type": "tool_result", "name": "knowledge", "result": g.ref_knowledge_event}


@dataclass
class _AssembledTools:
    """Saída da fase de montagem: specs de tools + seção de ferramentas do system."""
    tools: list[Any] = field(default_factory=list)
    sift_prompt: str = ""
    has_tools: bool = False
    skills_by_slug: dict[str, dict[str, Any]] = field(default_factory=dict)
    genimage_on: bool = False
    kb_tool_on: bool = False
    brain_on: bool = False
    brain_write: bool = False
    skill_learning_on: bool = False
    subagents_on: bool = False
    subagents_by_key: dict[str, dict[str, Any]] = field(default_factory=dict)


def _assemble_tools_and_prompt(
    *,
    sift: Any | None,
    use_tools: bool,
    code_mode: bool,
    skills: list[dict[str, Any]],
    genimage: dict[str, Any] | None,
    kb_tool_on: bool,
    brain: dict[str, Any] | None,
    skill_learning: bool | None,
    subagents: list[dict[str, Any]],
    run_subagent: Any | None,
) -> _AssembledTools:
    """Fase 2 — monta a lista de tools anunciadas ao modelo e a seção de
    ferramentas do system prompt (SIFT + skills + genimage + KB-tool + delegate)."""
    a = _AssembledTools()
    # em code mode o modelo recebe run_code (orquestra várias tools escrevendo
    # Python numa chamada só) em vez de execute_tool
    a.has_tools = sift is not None and use_tools
    # metadados do integrador no escopo (scope.meta, oficial na SIFT >= 0.7):
    # catálogo legível, modo de exposição, prompt "quando usar", tools promovidas
    sift_meta: dict[str, Any] = (getattr(sift, "meta", None) or {}) if a.has_tools else {}
    if a.has_tools and code_mode:
        # SIFT >= 0.8: o code_system_prompt já traz as SANDBOX_RULES (geradas da própria
        # policy do sandbox, então não envelhecem) e a semântica de REPL do `output`.
        a.sift_prompt = sift.code_system_prompt
        a.tools = list(sift.code_tools())
        # tools LONGAS promovidas a 1ª classe (rodam fora do sandbox do run_code —
        # o watchdog de parede mataria o filho e descartaria o resultado)
        extra = sift_meta.get("code_extra_tools")
        if extra:
            a.tools += list(extra)
            # Sem esta nota, a tool promovida é a ÚNICA "de busca" visível direto e o
            # modelo a usava p/ QUALQUER pesquisa (ex.: deep research sem ser pedida) —
            # a alternativa barata (web.search dentro do run_code) fica invisível.
            names = ", ".join(
                n for n in (
                    ((t.get("function") or {}).get("name") or "") for t in extra
                ) if n
            )
            if names:
                a.sift_prompt += "\n\n" + _PROMOTED_TOOLS_NOTE.format(names=names)
    elif a.has_tools:
        a.sift_prompt = sift.system_prompt
        # SIFT >= 0.7: pins são por-escopo — openai_tools() do scope já inclui as
        # ferramentas fixadas como specs de 1ª classe (nome flat)
        a.tools = list(sift.openai_tools())

    # Como o SIFT é apresentado ao modelo — o "QUANDO usar" (o "COMO usar" já é
    # resolvido pelas meta-ferramentas). O catálogo é injetado nos dois modos (o modelo
    # precisa saber o que tem); "list" reforça o texto. O guard de ação é sempre anexado.
    if a.has_tools:
        mode = sift_meta.get("sift_mode") or "prompt"
        catalog = sift_meta.get("catalog") or []
        meta = "run_code" if code_mode else "execute_tool"
        custom = (sift_meta.get("sift_prompt") or "").strip() or DEFAULT_TOOL_PROMPT
        a.sift_prompt = _compose_tool_prompt(a.sift_prompt, catalog, mode, custom, meta)

    # Tools NATIVAS (fora do índice SIFT) anunciadas ao modelo no fim da montagem:
    # elas já estão no array de tools, mas o prompt do SIFT ensina que o caminho para
    # achar ferramentas é `search_tools` — que NÃO as indexa. Sem este aviso o modelo
    # busca, não acha e responde "não tenho ferramenta pra isso" (bug real: pediram uma
    # foto da base de conhecimento e ele negou tendo `search_knowledge` na mão).
    native_names: list[str] = []

    # Skills (independentes do SIFT): o modelo vê só nome+descrição e carrega o
    # conteúdo completo sob demanda via view_skill.
    if skills:
        a.tools = list(a.tools) + [_view_skill_tool()]
        native_names.append("view_skill")
        for s in skills:
            a.skills_by_slug[str(s.get("slug"))] = s
            nm = str(s.get("name") or "").strip().lower()
            if nm:
                a.skills_by_slug.setdefault(nm, s)

    # GenImage Router: com o filtro ativo + modelo configurado, o modelo ganha a
    # tool `generate_image` (independe da SIFT — como o view_skill).
    a.genimage_on = bool(genimage and genimage.get("model"))
    if a.genimage_on:
        a.tools = list(a.tools) + [_generate_image_tool()]
        native_names.append("generate_image")

    # Base de Conhecimento no modo "ferramenta": o modelo ganha `search_knowledge`
    # (independe da SIFT), buscando os documentos sob demanda.
    a.kb_tool_on = kb_tool_on
    if a.kb_tool_on:
        a.tools = list(a.tools) + [_search_knowledge_tool()]
        native_names.append("search_knowledge")

    # Second brain: com cérebros acoplados, o modelo ganha a tool `brain`
    # (list/search/read e, com escrita liberada, write) — independe da SIFT.
    a.brain_on = bool(brain and brain.get("brains"))
    a.brain_write = a.brain_on and bool(brain.get("write"))
    if a.brain_on:
        a.tools = list(a.tools) + [_brain_tool(a.brain_write)]
        native_names.append("brain")

    # /learn: proposta de skill destilada do trabalho do chat (proposal-only —
    # o usuário aprova num card; o modelo NUNCA salva). Tri-state: True força,
    # False desliga, None (default) = AUTO — só injeta se o turno JÁ anuncia outras
    # tools (injetar `tools` num modelo sem suporte a tool-calling quebra o request).
    a.skill_learning_on = skill_learning is True or (skill_learning is None and bool(a.tools))
    if a.skill_learning_on:
        a.tools = list(a.tools) + [_propose_skill_tool()]

    # Subagentes: com a permissão ligada + um time resolvido, o modelo (orquestrador)
    # ganha a tool `delegate` p/ acionar operários (cada um um ModelConfig próprio).
    a.subagents_by_key = {str(x.get("key")): x for x in subagents}
    a.subagents_on = bool(subagents and run_subagent is not None)
    if a.subagents_on:
        a.tools = list(a.tools) + [_delegate_tool(subagents)]
        native_names.append("delegate")

    # aviso das tools nativas: só faz sentido com a SIFT ligada (é o prompt dela que
    # ensina o `search_tools` como caminho único de descoberta).
    if a.has_tools and native_names:
        a.sift_prompt = "\n\n".join(
            p for p in (a.sift_prompt, _native_tools_note(native_names)) if p
        )
    return a


async def _append_user_message(
    st: dict[str, int],
    messages: list[dict[str, Any]],
    *,
    user_text: str,
    media: MediaOpts,
    api_key: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Fase 3 — mensagem do usuário + anexos (arquivos, áudio, imagens).

    Aplica os roteadores de mídia (Audio/Vision Router, OCR) e anexa a mensagem
    final em `messages`; o total de chars de anexos sai em `st["attach_chars"]`."""
    attachments = media.attachments or []
    images = [a for a in attachments if a.get("type") == "image" and a.get("url")]
    audios = [a for a in attachments if a.get("type") == "audio" and a.get("url")]
    files = [a for a in attachments if a.get("type") == "file" and a.get("text")]
    file_blocks = "\n\n".join(
        f"[Arquivo anexado: {a.get('name') or 'arquivo'}]\n{a['text']}" for a in files
    )
    base_text = user_text
    if file_blocks:
        base_text = (base_text + "\n\n" + file_blocks).strip() if base_text else file_blocks

    # Audio Router: transcreve os áudios ANTES do tratamento de imagens, para a
    # transcrição entrar no texto-base em qualquer um dos ramos abaixo.
    audio_note = ""
    if audios and media.audio_router:
        yield {"type": "audio_router", "status": "start", "engine": media.audio_router.get("engine"), "count": len(audios)}
        try:
            tx = await _transcribe_audios(media.audio_router, audios, api_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audio Router falhou (%s); seguindo sem transcrição", exc)
            tx = ""
        yield {"type": "audio_router", "status": "done", "count": len(audios)}
        audio_note = (
            f"[O usuário enviou {len(audios)} áudio(s). Transcrição:\n{tx}]"
            if tx else "[O usuário enviou áudio(s), mas não foi possível transcrevê-los.]"
        )
    elif audios:
        audio_note = "[O usuário enviou áudio(s), mas este modelo não tem Audio Router configurado.]"
    if audio_note:
        base_text = (base_text + "\n\n" + audio_note).strip() if base_text else audio_note

    # precedência do tratamento de imagens (modelo sem visão nativa):
    #  - motor "tesseract" (ou "vision" sem router configurado) → OCR local
    #  - motor "vision" com Vision Router → o modelo de visão descreve/transcreve
    use_tesseract = bool(
        images and media.ocr and (media.ocr_engine == "tesseract" or not media.vision_router_model)
    )
    attach_chars = len(file_blocks) + len(audio_note)
    if images and media.vision:
        # visão nativa: manda as imagens como partes image_url (multipart OpenAI)
        parts: list[dict[str, Any]] = []
        if base_text:
            parts.append({"type": "text", "text": base_text})
        for a in images:
            parts.append({"type": "image_url", "image_url": {"url": a["url"]}})
        messages.append({"role": "user", "content": parts})
    elif images and media.vision_router_model and not use_tesseract:
        # Vision Router: um modelo com visão descreve/transcreve as imagens em texto
        yield {"type": "vision_router", "model": media.vision_router_model, "count": len(images)}
        try:
            desc = await _describe_images(api_key, media.vision_router_model, images)
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
                t = await run_in_threadpool(extraction.ocr_image_bytes, raw, media.ocr_lang)
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
    st["attach_chars"] = attach_chars


def _finalize_usage(
    total_usage: dict[str, float],
    *,
    input_chars: dict[str, int],
    tool_result_chars: dict[str, int],
    extra_breakdown: dict[str, int] | None,
    reasoning_text: str,
    tool_events: list[dict[str, Any]] | None = None,
    system_chars: dict[str, int] | None = None,
    tools_prompt_chars: dict[str, int] | None = None,
    calls_log: list[dict[str, Any]] | None = None,
) -> None:
    """Fase 5 — detalhamentos de uso (entrada/saída/por-tool/extra) em `total_usage`.

    ENTRADA por categoria: distribui o total real de prompt_tokens proporcionalmente
    ao tamanho (chars) de cada bloco. SAÍDA: visível vs raciocínio (thinking)."""
    prompt_total = int(total_usage.get("prompt_tokens", 0) or 0)
    weight_total = sum(input_chars.values())
    rate = (prompt_total / weight_total) if (prompt_total and weight_total) else 0.0
    # custo POR EVENTO de ferramenta (badge na UI): a mesma taxa tokens/char do turno
    # aplicada ao tamanho de cada chamada/resultado. Somados, os resultados de uma tool
    # batem com `tools_breakdown[tool]` — é a mesma distribuição, só granular.
    if prompt_total and weight_total and tool_events:
        for ev in tool_events:
            chars = ev.get("chars")
            if chars:
                ev["tokens"] = round(prompt_total * chars / weight_total)
    if prompt_total and weight_total:
        input_breakdown = {
            k: round(prompt_total * v / weight_total) for k, v in input_chars.items()
        }
    else:
        input_breakdown = {k: 0 for k in input_chars}

    completion_total = int(total_usage.get("completion_tokens", 0) or 0)
    reasoning_tokens = int(total_usage.get("reasoning_tokens", 0) or 0)
    # fallback: se o provedor não separou, estima o thinking pelo texto capturado
    if not reasoning_tokens and reasoning_text and completion_total:
        est = round(len(reasoning_text) / 4)
        reasoning_tokens = min(est, completion_total)
    total_usage["input_breakdown"] = input_breakdown
    total_usage["output_breakdown"] = {
        "output": max(0, completion_total - reasoning_tokens),
        "thinking": reasoning_tokens,
    }
    # por ferramenta (mesma distribuição proporcional; soma ≈ tool_results)
    if prompt_total and weight_total and tool_result_chars:
        total_usage["tools_breakdown"] = {
            k: round(prompt_total * v / weight_total) for k, v in tool_result_chars.items()
        }
    # detalhe do "extra" por origem (artefatos/canal/guardas) — soma ≈ extra
    if prompt_total and weight_total and extra_breakdown:
        total_usage["extra_breakdown"] = {
            k: round(prompt_total * v / weight_total)
            for k, v in extra_breakdown.items() if v
        }
    # PROVENIÊNCIA do prompt do sistema (do modelo/agente vs. injetado por nós) e do
    # bloco de ferramentas (instruções vs. schemas vs. cérebro) — somam a `system`/`tools`
    if rate and system_chars:
        total_usage["system_breakdown"] = {k: round(rate * v) for k, v in system_chars.items()}
    if rate and tools_prompt_chars:
        total_usage["tools_prompt_breakdown"] = {k: round(rate * v) for k, v in tools_prompt_chars.items()}
    # ferramentas REAIS executadas (agrega o log do on_result). No Modo Código é a ÚNICA
    # forma de ver o que rodou dentro do run_code — ali `tools_breakdown` só diz "run_code".
    if rate and calls_log:
        agg: dict[str, int] = {}
        for c in calls_log:
            path = str(c.get("path") or "")
            if path:
                agg[path] = agg.get(path, 0) + int(c.get("chars") or 0)
        if agg:
            total_usage["called_tools_breakdown"] = {k: round(rate * v) for k, v in agg.items()}


_SCOPE_ERROR_HINT = (
    "This tool path does not exist here. Call search_tools with a short query to "
    "discover the CORRECT path, then retry execute_tool — do not tell the user the "
    "tool is unavailable."
)

# Teto de tools rodando ao mesmo tempo numa volta. Existe para não transformar um
# fan-out do modelo (ex.: "leia estes 20 e-mails") numa rajada contra a API de
# terceiros — que responderia com rate limit.
_MAX_PARALLEL_TOOLS = 6


@dataclass
class _ToolDispatcher:
    """Executa UMA tool_call do loop agêntico, emitindo os eventos de progresso na
    ordem certa (streaming) e deixando o resultado CRU em `self.result`. Agrupa o
    contexto do turno (SIFT, skills, genimage, KB, subagentes) construído uma vez
    antes do loop. As tools "internas" (view_skill/generate_image/search_knowledge/
    delegate) são tratadas aqui; o resto cai no dispatch da SIFT.

    Uso: `async for ev in disp.run(name, args, tc): yield ev` e então `disp.result`.
    O `delegations_used` persiste entre chamadas; `delegate_pre` (resultados da
    delegação PARALELA) é setado por iteração antes de despachar."""
    sift: Any
    code_mode: bool
    api_key: str
    user_id: str
    chat_id: str | None
    skills: list[dict[str, Any]]
    skills_by_slug: dict[str, dict[str, Any]]
    genimage_on: bool
    genimage: dict[str, Any] | None
    kb_tool_on: bool
    kb_bases: list[str]
    kb_k: int
    user_text: str
    brain_on: bool
    brain_write: bool
    brain_ids: list[str]
    brain_names: list[str]
    brain_k: int
    skill_learning_on: bool
    subagents_on: bool
    subagents_by_key: dict[str, dict[str, Any]]
    subagent_max_calls: int
    subagent_pass_context: bool
    subagent_worker_memory: bool
    run_subagent: Any
    delegations_used: int = 0
    delegate_pre: dict[str, dict] = field(default_factory=dict)
    result: Any = None

    async def run(self, name: str, args: dict, tc: dict) -> AsyncGenerator[dict[str, Any], None]:
        if name == "view_skill":
            self.result = self._view_skill(args)
        elif name == "generate_image":
            async for ev in self._generate_image(args):
                yield ev
        elif name == "search_knowledge":
            async for ev in self._search_knowledge(args):
                yield ev
        elif name == "brain":
            async for ev in self._brain(args):
                yield ev
        elif name == "propose_skill":
            self.result = self._propose_skill(args)
        elif name == "delegate":
            async for ev in self._delegate(args, tc):
                yield ev
        elif self.sift is None:
            self.result = {"error": "ferramentas indisponíveis"}
        elif name == "run_code" and not self.code_mode:
            # run_code não foi anunciado a este modelo; não executa código
            self.result = {"error": "run_code não está habilitado para este modelo"}
        else:
            self.result = await self._sift_dispatch(name, args)

    def _view_skill(self, args: dict) -> Any:
        slug = str(args.get("slug") or args.get("name") or "").strip().lower()
        sk = self.skills_by_slug.get(slug)
        if sk is None:
            known = ", ".join(sorted({s["slug"] for s in self.skills})) or "(nenhuma)"
            return {"error": f"skill '{slug}' não encontrada. Disponíveis: {known}"}
        return {"slug": sk["slug"], "name": sk["name"], "content": sk.get("content") or ""}

    async def _generate_image(self, args: dict) -> AsyncGenerator[dict[str, Any], None]:
        # GenImage Router: gera a imagem, guarda os bytes e devolve uma URL assinada
        # (pequena) — o base64 NUNCA vai ao contexto do modelo.
        prompt = str(args.get("prompt") or "").strip()
        if not self.genimage_on:
            self.result = {"error": "GenImage Router não está ativo neste modelo"}
            return
        if not prompt:
            self.result = {"error": "`prompt` é obrigatório"}
            return
        yield {"type": "image_gen", "status": "start", "prompt": prompt[:120]}
        try:
            keys = {"openrouter": self.api_key, "imagegen": (self.genimage or {}).get("imagegen_key")}
            img_bytes, mime = await image_gen.generate(
                self.genimage, keys, prompt, str(args.get("size") or "1024x1024")
            )
            image_id = await _save_generated_image(
                self.user_id, self.chat_id, mime, img_bytes, prompt, (self.genimage or {}).get("model", "")
            )
            self.result = {"kind": "image", "url": image_gen.sign_image_url(image_id), "prompt": prompt}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao gerar imagem: %s", exc)
            yield {"type": "image_gen", "status": "error"}
            self.result = {"error": f"não foi possível gerar a imagem: {exc}"}

    async def _search_knowledge(self, args: dict) -> AsyncGenerator[dict[str, Any], None]:
        # Base de Conhecimento (modo ferramenta): busca os documentos e devolve os
        # trechos numerados; a UI mostra as fontes (via kind:"knowledge").
        query = str(args.get("query") or "").strip() or self.user_text
        if not self.kb_tool_on:
            self.result = {"error": "base de conhecimento não está ativa neste modelo"}
            return
        # o MODELO decide quanto quer ver (o k do modelo é só o padrão); teto de 20
        # para um `limit` alucinado não estourar o contexto do turno.
        try:
            k = int(args.get("limit") or self.kb_k)
        except (TypeError, ValueError):
            k = self.kb_k
        k = max(1, min(k, 20))
        yield {"type": "knowledge", "status": "start", "query": query[:120]}
        try:
            kres = await kb_retrieval.search(self.user_id, self.kb_bases, query, k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_knowledge falhou: %s", exc)
            kres = []
        _blk, ksrc = _knowledge_block_and_sources(kres)
        # texto lido pelo MODELO => inglês (padrão do projeto). No caso VAZIO ele
        # instrui a re-tentar em vez de declarar ausência: um único miss de busca
        # semântica virava "você não tem esse arquivo" (bug observado).
        model_txt = (
            "Passages from the user's knowledge base (cite the one you use with [n]):\n\n" + _blk
            if kres else
            "No passage matched THIS query. That does not mean the file is absent — "
            "retry with different wording (synonyms, the file's own language, a broader "
            "term) or a larger `limit` before telling the user it doesn't exist."
        )
        self.result = {
            "kind": "knowledge", "query": query[:200],
            "sources": ksrc, "count": len(kres), "_model": model_txt,
        }

    async def _brain(self, args: dict) -> AsyncGenerator[dict[str, Any], None]:
        # Second brain: notas markdown [[interligadas]] (kind="brain" nas
        # knowledge_bases). Leitura sempre; escrita só com `brain_write`.
        action = str(args.get("action") or "").strip().lower()
        if not self.brain_on:
            self.result = {"error": "the second brain is not enabled for this model"}
            return
        if action == "list":
            docs = await brain_service.load_brain_docs(self.brain_ids, self.user_id)
            self.result = {
                "count": len(docs),
                "notes": [{"title": d["title"], "updated_at": d["updated_at"]} for d in docs],
            }
        elif action == "search":
            query = str(args.get("query") or "").strip() or self.user_text
            yield {"type": "knowledge", "status": "start", "query": query[:120]}
            try:
                kres = await kb_retrieval.search(self.user_id, self.brain_ids, query, self.brain_k)
            except Exception as exc:  # noqa: BLE001
                logger.warning("brain search falhou: %s", exc)
                kres = []
            _blk, ksrc = _knowledge_block_and_sources(kres)
            model_txt = (
                "Passages from the second brain (cite the source used with [n]):\n\n" + _blk
                if kres else "No relevant notes found in the second brain."
            )
            self.result = {
                "kind": "knowledge", "query": query[:200],
                "sources": ksrc, "count": len(kres), "_model": model_txt,
            }
        elif action == "read":
            title = str(args.get("title") or "").strip()
            docs = await brain_service.load_brain_docs(self.brain_ids, self.user_id)
            key = brain_service.note_key(title)
            note = next(
                (d for d in docs if brain_service.note_key(d["filename"]) == key), None
            ) or next((d for d in docs if brain_service.note_key(d["title"]) == key), None)
            if note is None:
                known = ", ".join(d["title"] for d in docs[:40]) or "(no notes yet)"
                self.result = {"error": f"note '{title}' not found. Known notes: {known}"}
                return
            backlinks = [
                d["title"] for d in docs
                if d["id"] != note["id"] and any(
                    brain_service.note_key(t) in (key, brain_service.note_key(note["title"]))
                    for t in brain_service.parse_links(d["text"])
                )
            ]
            self.result = {
                "title": note["title"], "content": note["text"],
                "links": brain_service.parse_links(note["text"]), "backlinks": backlinks,
            }
        elif action == "write":
            if not self.brain_write:
                self.result = {"error": "writing to the second brain is disabled for this model"}
                return
            title = str(args.get("title") or "").strip()
            content = str(args.get("content") or "")
            if not title or not content.strip():
                self.result = {"error": "`title` and `content` are required for write"}
                return
            # com vários cérebros, `brain` (nome) escolhe o destino; default = 1º
            target = self.brain_ids[0] if self.brain_ids else None
            want = str(args.get("brain") or "").strip().casefold()
            if want:
                for bid, bname in zip(self.brain_ids, self.brain_names):
                    if (bname or "").casefold() == want:
                        target = bid
                        break
            mode = "append" if str(args.get("mode") or "").lower() == "append" else "replace"
            yield {"type": "brain", "status": "start", "title": title[:120]}
            try:
                res = await brain_service.write_note(
                    self.user_id, target, title, content, mode
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("brain write falhou: %s", exc)
                yield {"type": "brain", "status": "error"}
                self.result = {"error": f"could not write the note: {exc}"}
                return
            self.result = {
                "kind": "brain_note",
                "doc_id": res["doc_id"], "base_id": res["base_id"],
                "title": res["title"], "action": res["action"],
                "preview": content.strip()[:280],
                "url": sign_doc_url(res["doc_id"]),
            }
        else:
            self.result = {"error": "unknown action; valid: list, search, read"
                           + (", write" if self.brain_write else "")}

    def _propose_skill(self, args: dict) -> Any:
        # /learn — proposal-only: monta a proposta p/ o card editável da UI.
        # NUNCA toca o banco; quem salva é o usuário (POST /skills no card).
        if not self.skill_learning_on:
            return {"error": "skill learning is not enabled for this model"}
        name = str(args.get("name") or "").strip()[:255]
        description = str(args.get("description") or "").strip()[:2000]
        content = str(args.get("content") or "").strip()[:200_000]
        if not name or not description or not content:
            return {"error": "`name`, `description` and `content` are required"}
        slug = re.sub(
            r"[^a-z0-9]+", "_", str(args.get("slug") or name).lower().strip()
        ).strip("_")[:64] or "skill"
        tags: list[str] = []
        for t in args.get("tags") or []:
            s = str(t).strip()[:40]
            if s and s not in tags:
                tags.append(s)
        return {
            "kind": "skill_proposal", "proposal_id": str(uuid.uuid4()),
            "slug": slug, "name": name, "description": description,
            "content": content, "tags": tags[:10],
        }

    async def _delegate(self, args: dict, tc: dict) -> AsyncGenerator[dict[str, Any], None]:
        if not self.subagents_on:
            self.result = {"error": "subagentes não habilitados neste modelo"}
            return
        if tc["id"] in self.delegate_pre:  # já rodou em paralelo
            self.result = self.delegate_pre[tc["id"]]
            yield {"type": "subagent", "status": "done", "agent": (self.result.get("agent") if isinstance(self.result, dict) else None) or str(args.get("agent") or "")}
            return
        if self.delegations_used >= self.subagent_max_calls:
            self.result = {"error": f"limite de {self.subagent_max_calls} delegações por turno atingido"}
            return
        key = str(args.get("agent") or "")
        task = str(args.get("task") or "")
        spec = self.subagents_by_key.get(key)
        if spec is None:
            self.result = {"error": f"subagente '{key}' não autorizado"}
            return
        yield {"type": "subagent", "status": "start", "agent": spec.get("name", key), "task": task[:200], "ctx": self.subagent_pass_context, "mem": self.subagent_worker_memory}
        self.delegations_used += 1
        try:
            self.result = await self.run_subagent(key, task)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Subagente falhou: %s", exc)
            self.result = {"error": f"o subagente falhou: {exc}"}
        yield {"type": "subagent", "status": "done", "agent": (self.result.get("agent") if isinstance(self.result, dict) else None) or spec.get("name", key)}

    async def _sift_dispatch(self, name: str, args: dict) -> Any:
        # NÃO trocar por sift.adispatch: na SIFT 0.8 ele roda tools SÍNCRONAS inline
        # ("offload them yourself if they block") — e TODAS as nossas builtins são
        # sync (requests/Google/Tuya, segundos cada) → bloquearia o event loop do
        # servidor inteiro. O threadpool é o offload correto enquanto as tools não
        # forem `async def`.
        result = await run_in_threadpool(self.sift.dispatch, name, args)
        # Recuperação: modelos fracos às vezes chamam o PATH da tool DIRETO como
        # nome da função (ex.: função "web.search.query" com {query,limit}) em vez
        # de execute_tool{path,params}. A SIFT devolve "unknown meta-tool"; nós
        # reroteamos via execute_tool numa retry, tratando os args como params.
        if (
            isinstance(result, str)
            and "unknown meta-tool" in result
            and "." in name
            and name not in {"search_tools", "execute_tool", "run_code", "get_tool_schema"}
        ):
            result = await run_in_threadpool(
                self.sift.dispatch, "execute_tool", {"path": name, "params": args}
            )
        # Path errado/fora do escopo (modelo chutou, ex.: 'web.read' em vez de
        # 'web.page.read'): enriquece o erro com o caminho de recuperação, senão
        # modelos fracos DESISTEM e dizem que a ferramenta não existe.
        if isinstance(result, str) and (
            "not allowed in this scope" in result or "unknown tool" in result.lower()
        ):
            try:
                _r = json.loads(result)
                if isinstance(_r, dict) and _r.get("error"):
                    _r["hint"] = _SCOPE_ERROR_HINT
                    result = json.dumps(_r, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass
        return result


def _shape_tool_result(result: Any) -> tuple[str, Any]:
    """A partir do resultado CRU de uma tool, devolve (content_p/_modelo, event_p/_UI).

    - str (JSON ou texto do search_tools): não re-serializa (evita duplo-encode);
    - dict: serializa p/ o modelo, e o próprio dict vai à UI;
    - artefatos que o FRONT renderiza mas o modelo NÃO deve reproduzir (imagem,
      rascunho de e-mail) recebem só uma nota enxuta;
    - conhecimento: o modelo recebe os TRECHOS (`_model`), a UI só as fontes."""
    if isinstance(result, str):
        content = result
        try:
            event_result: Any = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            event_result = result
    else:
        content = json.dumps(result, ensure_ascii=False, default=str)
        event_result = result
    if isinstance(event_result, dict) and event_result.get("kind") == "image":
        content = json.dumps({"ok": True, "note": "Image generated and shown to the user."})
    elif isinstance(event_result, dict) and event_result.get("kind") == "video":
        content = json.dumps({"ok": True, "note": "Video generated and shown to the user "
                                                  "in a player. Do not paste the url back."})
    elif isinstance(event_result, dict) and event_result.get("kind") == "email_draft":
        content = json.dumps({
            "ok": True,
            "note": "An editable email draft was shown to the user to review and send. "
                    "Do NOT claim the email was sent; the user will send it from the composer.",
        })
    elif isinstance(event_result, dict) and event_result.get("kind") == "skill_proposal":
        content = json.dumps({
            "ok": True,
            "note": "An editable skill proposal card was shown to the user to review "
                    "and approve. Do NOT claim the skill was saved; the user decides "
                    "in the card.",
        })
    elif isinstance(event_result, dict) and event_result.get("kind") == "brain_note":
        content = json.dumps({
            "ok": True,
            "note": f"Note '{event_result.get('title')}' was "
                    f"{'updated' if event_result.get('action') == 'updated' else 'created'} "
                    "in the second brain and a card was shown to the user.",
        })
    elif isinstance(event_result, dict) and event_result.get("kind") == "knowledge":
        content = event_result.get("_model") or ""
        event_result = {k: v for k, v in event_result.items() if k != "_model"}
    return content, event_result


async def run_turn(
    *,
    api_key: str,
    model: str,
    history: list[dict[str, Any]],
    user_text: str,
    chat_system_prompt: str | None,
    params: dict[str, Any],
    session: TurnSession,
    base_url: str | None = None,
    sift: Any | None = None,
    use_tools: bool = True,
    code_mode: bool = False,
    skills: list[dict[str, Any]] | None = None,
    use_context: bool = True,
    knowledge: dict[str, Any] | None = None,
    # second brain: {"brains": [ids], "names": [nomes], "write": bool, "k": int}
    # (de _resolve_brain + nomes). None/vazio = tool `brain` não é injetada.
    brain: dict[str, Any] | None = None,
    # /learn tri-state: True força a tool propose_skill, False desliga,
    # None (default) = auto — injeta só se o turno já anuncia outras tools.
    skill_learning: bool | None = None,
    # capacidade "Data e Hora em Tempo Real": injeta a linha de data/hora a cada
    # turno. Default True (comportamento antigo); False economiza os ~30-40 tokens.
    realtime_datetime: bool = True,
    # docs referenciados com "#" no compositor: [{id, filename, base_id, text}].
    # Injetados neste turno (híbrido: texto inteiro se pequeno, senão trechos).
    ref_docs: list[dict[str, Any]] | None = None,
    extra_system: str | None = None,
    # detalhamento (em chars) das origens do extra_system, p/ o painel de uso:
    # {"artifacts": n, "channel": n, "guards": n} — só rotula, não muda o prompt
    extra_breakdown: dict[str, int] | None = None,
    memory: MemoryOpts | None = None,
    media: MediaOpts | None = None,
    subagent: SubagentOpts | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    settings = get_settings()

    # aliases dos grupos usados direto no corpo (o resto vive nas fases)
    user_id = session.user_id
    user_tz = session.user_tz
    user_tz_offset = session.user_tz_offset
    chat_id = session.chat_id
    agent_id = session.agent_id
    _mem = memory or MemoryOpts()
    mem_write, mem_review, mem_project = _mem.write, _mem.review, _mem.project
    _md = media or MediaOpts()
    genimage, image_output = _md.genimage, _md.image_output
    _sa = subagent or SubagentOpts()
    subagents, run_subagent = _sa.agents, _sa.run
    subagent_mode, subagent_max_calls = _sa.mode, _sa.max_calls
    subagent_pass_context, subagent_worker_memory = _sa.pass_context, _sa.worker_memory

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
    toolctx.background.set(bool(session.background))
    # perfil do usuário visível à tool user.profile.get (nome, sobre, nascimento…)
    toolctx.user_profile.set(session.user_profile or {})
    # projeto do Codespace vinculado a este chat, visível às tools code.graph/code.files
    toolctx.current_codespace_project_id.set(session.codespace_project_id)

    # 1. contexto do turno: memória (mem0) + Base de Conhecimento (auto) + "#"refs
    g = _GatheredContext()
    async for ev in _gather_context(
        g, api_key=api_key, user_text=user_text, session=session,
        memory=_mem, knowledge=knowledge, ref_docs=ref_docs,
    ):
        yield ev
    mem_items, memories = g.mem_items, g.memories
    knowledge_block, ref_block = g.knowledge_block, g.ref_block
    auto_knowledge_event, ref_knowledge_event = g.auto_knowledge_event, g.ref_knowledge_event
    # o search_knowledge (modo "tool") busca SÓ nas bases marcadas como ferramenta
    kb_bases, kb_k = g.kb_bases_tool, g.kb_k

    # 2. montagem das tools anunciadas + seção de ferramentas do system prompt
    skills = skills or []
    subagents = subagents or []
    asm = _assemble_tools_and_prompt(
        sift=sift, use_tools=use_tools, code_mode=code_mode, skills=skills,
        genimage=genimage, kb_tool_on=bool(g.kb_bases_tool),
        brain=brain, skill_learning=skill_learning,
        subagents=subagents, run_subagent=run_subagent,
    )
    tools: Any = asm.tools
    sift_prompt = asm.sift_prompt
    has_tools = asm.has_tools
    skills_by_slug = asm.skills_by_slug
    genimage_on = asm.genimage_on
    kb_tool_on = asm.kb_tool_on
    subagents_on = asm.subagents_on
    subagents_by_key = asm.subagents_by_key
    # despachante das tool_calls (contexto do turno agrupado; contador de
    # delegações compartilhado entre a delegação paralela e a sequencial)
    _brain_cfg = brain or {}
    disp = _ToolDispatcher(
        sift=sift, code_mode=code_mode, api_key=api_key, user_id=user_id, chat_id=chat_id,
        skills=skills, skills_by_slug=skills_by_slug,
        genimage_on=genimage_on, genimage=genimage,
        kb_tool_on=kb_tool_on, kb_bases=kb_bases, kb_k=kb_k, user_text=user_text,
        brain_on=asm.brain_on, brain_write=asm.brain_write,
        brain_ids=[str(b) for b in _brain_cfg.get("brains") or []],
        brain_names=[str(n) for n in _brain_cfg.get("names") or []],
        brain_k=int(_brain_cfg.get("k") or 6),
        skill_learning_on=asm.skill_learning_on,
        subagents_on=subagents_on, subagents_by_key=subagents_by_key,
        subagent_max_calls=subagent_max_calls,
        subagent_pass_context=subagent_pass_context,
        subagent_worker_memory=subagent_worker_memory, run_subagent=run_subagent,
    )

    static_system = _build_static_system(chat_system_prompt, sift_prompt)
    skills_block = _skills_block(skills)
    if skills_block:
        static_system = (static_system + "\n\n" + skills_block).strip()
    brain_block = (
        _brain_block(list(_brain_cfg.get("names") or []), asm.brain_write)
        if asm.brain_on else ""
    )
    if brain_block:
        static_system = (static_system + "\n\n" + brain_block).strip()
    mem_block = _memory_block(memories)
    # bloco de contexto por-turno (fora do prefixo cacheado): memória + conhecimento
    # recuperado (modo auto). Ambos variam a cada turno conforme a pergunta.
    context_block = "\n\n".join(b for b in (mem_block, knowledge_block, ref_block) if b)
    time_note = _temporal_note(user_tz, user_tz_offset) if realtime_datetime else ""
    # `extra_system` = instruções de ALTA PRIORIDADE: reforço de um Guarda de saída
    # (retry) OU instruções do canal (ex.: WhatsApp). Vão para o FIM do system, DEPOIS
    # de memória/hora e com um marcador de prioridade — é a última coisa que o modelo
    # lê (maior peso/recência) e fica FORA do prefixo cacheável (o guarda reescreve a
    # cada tentativa; assim o prefixo grande e estável continua sendo reaproveitado).
    # Ver run_turn_guarded.
    messages: list[dict[str, Any]] = [
        _system_message(static_system, context_block, model, time_note, extra_system)
    ]
    # capacidade "Contexto do Chat": quando desligada, o modelo NÃO recebe o
    # histórico (turno stateless — só system + mensagem atual).
    if use_context:
        messages.extend(history)

    # 3. mensagem do usuário + anexos (arquivos, áudio, imagens) — Fase 3
    _att: dict[str, int] = {"attach_chars": 0}
    async for ev in _append_user_message(
        _att, messages, user_text=user_text, media=_md, api_key=api_key,
    ):
        yield ev
    attach_chars = _att["attach_chars"]

    # Pesos (em caracteres) de cada origem do prompt, p/ atribuir os tokens de
    # ENTRADA por categoria. O total de prompt_tokens vem real do provedor; a
    # divisão é proporcional ao tamanho de cada bloco (estimativa honesta).
    # Separamos os "custos invisíveis": o CONTEXTO (histórico reenviado a cada
    # turno) e os RESULTADOS de ferramentas (injetados no loop) — que crescem
    # sem o usuário perceber — do input real digitado no promptbox.
    #   user          = mensagem atual (promptbox)
    #   context       = histórico do chat reenviado neste turno
    #   system        = prompt do sistema do chat (+ nota temporal)
    #   extra         = instruções extras do canal/guardas/artefatos (extra_system)
    #   memory        = memórias recuperadas do mem0
    #   tools         = system_prompt do SIFT + catálogo + schemas das tools
    #   skills        = bloco de skills equipadas (nome+descrição)
    #   tool_results  = saídas das ferramentas injetadas durante o loop agêntico
    #   file          = anexos (arquivos + descrição de imagens do Vision Router)
    input_chars = {
        "user": len(user_text),
        "context": sum(len(str(m.get("content") or "")) for m in history) if use_context else 0,
        "system": len(chat_system_prompt or "") + len(time_note),
        "extra": len(extra_system or ""),
        "memory": len(mem_block),
        "knowledge": len(knowledge_block) + len(ref_block),
        "tools": len(sift_prompt) + (len(json.dumps(tools)) if tools else 0) + len(brain_block),
        "skills": len(skills_block),
        "tool_results": 0,  # preenchido conforme as tools respondem no loop (inclui view_skill)
        "file": attach_chars,
    }
    # PROVENIÊNCIA dos dois blocos compostos, p/ o detalhamento "Extenso" responder
    # "de onde veio esse prompt?": `system` mistura o prompt do modelo/agente com a
    # nota temporal que INJETAMOS a cada turno; `tools` mistura as instruções da SIFT
    # com os schemas das ferramentas (que o modelo nem lê como texto) e o cérebro.
    system_chars = {
        "model_prompt": len(chat_system_prompt or ""),
        "datetime": len(time_note),
    }
    tools_prompt_chars = {
        "instructions": len(sift_prompt),
        "schemas": len(json.dumps(tools)) if tools else 0,
        "brain": len(brain_block),
    }
    # por FERRAMENTA: quanto cada tool devolveu (p/ o detalhamento "Extenso")
    tool_result_chars: dict[str, int] = {}
    # ferramentas REAIS do turno (inclui as chamadas dentro do run_code — ver
    # sift_service.tool_calls_log). Sem isto o Modo Código é caixa-preta na conta.
    calls_log: list[dict[str, Any]] = []
    calls_token = sift_service.tool_calls_log.set(calls_log)

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
    # modo "auto" da Base de Conhecimento: registra as fontes recuperadas (já
    # emitidas acima) p/ persistirem na mensagem (SourcesBar após F5).
    if auto_knowledge_event is not None:
        tool_events.append({"kind": "result", "name": "knowledge", "data": auto_knowledge_event})
    # fontes dos arquivos referenciados com "#" — persistem p/ o SourcesBar após F5
    if ref_knowledge_event is not None:
        tool_events.append({"kind": "result", "name": "knowledge", "data": ref_knowledge_event})
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
    for _iter in range(settings.max_tool_iterations):
        # última rodada permitida (quando há mais de uma): retira as tools para
        # OBRIGAR uma resposta final. Sem isto, um modelo que continua chamando tools
        # até o teto encerra o loop com texto vazio — o usuário veria os cards das
        # tools e nenhuma resposta.
        if _iter > 0 and _iter == settings.max_tool_iterations - 1:
            tools = None
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

        # fecha o span de raciocínio ao FIM de cada iteração: quando o modelo pensa
        # e emite só tool_calls (sem texto), reasoning_started ficava aberto e o
        # próximo `+=` incluía a execução da(s) tool(s) e o pensamento seguinte —
        # inflando o "pensou por Xs". Cada iteração fecha o seu.
        if reasoning_started is not None:
            reasoning_seconds += time.monotonic() - reasoning_started
            reasoning_started = None

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
        # roda-os concorrentemente (respeitando o teto de chamadas do turno). O
        # contador de delegações é do dispatcher (compartilhado com o modo sequencial).
        disp.delegate_pre = {}
        if subagents_on and subagent_mode == "parallel":
            dcalls = [tc for tc in tool_calls if tc["function"]["name"] == "delegate"]
            if len(dcalls) > 1:
                picked: list[tuple[str, str, str]] = []
                for tc in dcalls:
                    if disp.delegations_used >= subagent_max_calls:
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
                    disp.delegations_used += 1
                    picked.append((tc["id"], key, task))
                if picked:
                    results = await asyncio.gather(
                        *[run_subagent(k, t) for (_i, k, t) in picked], return_exceptions=True
                    )
                    for (tcid, _k, _t), res in zip(picked, results):
                        disp.delegate_pre[tcid] = {"error": str(res)} if isinstance(res, Exception) else res

        def _args_of(tc: dict) -> dict:
            try:
                return json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                return {}

        def _announce(name: str, args: dict) -> dict[str, Any]:
            # `chars` = peso do bloco no contexto; vira o badge de tokens no
            # _finalize_usage (a chamada também é reenviada nas voltas seguintes).
            tool_events.append({
                "kind": "call", "name": name, "data": args,
                "chars": len(json.dumps(args, ensure_ascii=False, default=str)),
            })
            return {"type": "tool_call", "name": name, "arguments": args}

        def _absorb(name: str, tc: dict, result: Any) -> dict[str, Any]:
            """Registra o resultado de UMA tool: evento p/ a UI, mensagem p/ o modelo e
            a contabilidade de tokens. Devolve o evento a emitir."""
            content, event_result = _shape_tool_result(result)
            tool_events.append({
                "kind": "result", "name": name, "data": event_result,
                "chars": len(content),  # o que de fato volta como ENTRADA do modelo
            })
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": content})
            input_chars["tool_results"] += len(content)  # custo invisível: saída da tool volta como entrada
            tool_result_chars[name] = tool_result_chars.get(name, 0) + len(content)
            return {"type": "tool_result", "name": name, "result": event_result}

        # PARALELO: o modelo pede várias tools numa mensagem só (parallel tool calling).
        # Executá-las em série não custa tokens nem voltas — custa TEMPO DE PAREDE: ler 4
        # e-mails vira 4x a latência de rede enfileirada. Aqui elas rodam concorrentes.
        # `delegate` fica de fora: já tem o próprio paralelismo (delegate_pre) e um contador
        # compartilhado no dispatcher. Cada chamada ganha uma CÓPIA do dispatcher — ele
        # guarda o resultado em `self.result`, e instâncias concorrentes se atropelariam.
        specs = [(tc, tc["function"]["name"], _args_of(tc)) for tc in tool_calls]
        parallel = len(specs) > 1 and all(n != "delegate" for _t, n, _a in specs)

        if parallel:
            for _tc, name, args in specs:
                yield _announce(name, args)

            sem = asyncio.Semaphore(_MAX_PARALLEL_TOOLS)

            async def _run_one(tc: dict, name: str, args: dict):
                async with sem:
                    d = replace(disp, result=None)
                    evs = [ev async for ev in d.run(name, args, tc)]
                    return evs, d.result

            outs = await asyncio.gather(
                *[_run_one(tc, n, a) for tc, n, a in specs], return_exceptions=True
            )
            # emite na ORDEM ORIGINAL (os eventos de progresso de cada tool saem juntos):
            # a UI e o histórico ficam determinísticos, mesmo com a execução embaralhada.
            for (tc, name, _args), out in zip(specs, outs):
                if isinstance(out, BaseException):
                    logger.warning("Tool '%s' falhou em paralelo: %s", name, out)
                    yield _absorb(name, tc, {"error": str(out)})
                    continue
                evs, res = out
                for ev in evs:
                    yield ev
                yield _absorb(name, tc, res)
        else:
            for tc, name, args in specs:
                yield _announce(name, args)
                # despacha a tool (emite os eventos de progresso na ordem certa)
                async for ev in disp.run(name, args, tc):
                    yield ev
                yield _absorb(name, tc, disp.result)

        assistant_text = ""  # reinicia p/ a próxima volta (resposta final)

    # 5. memória pós-turno (só em chats persistentes; escopo escolhido pelo chat).
    # Dispara em BACKGROUND: não deve atrasar o `done`/conclusão visível na UI.
    if assistant_text and chat_id and mem_write and mem_write != "off":
        _spawn_memory_write(api_key, user_text, assistant_text, user_id, chat_id, agent_id, mem_write, mem_review, project_id=mem_project)

    # 5b. Aprendizado Proativo (Curator): revisão em background a cada N turnos —
    # propõe skills e cura memória (opt-in; a checagem do toggle é feita na task).
    if assistant_text and chat_id:
        curator.maybe_review(
            api_key, user_id, chat_id, agent_id, model, base_url,
            curator.turn_signals(user_text, tool_events),
        )

    # 5. detalhamentos de uso (entrada/saída/por-tool/extra) — Fase 5
    _finalize_usage(
        total_usage, input_chars=input_chars, tool_result_chars=tool_result_chars,
        extra_breakdown=extra_breakdown, reasoning_text=reasoning_text,
        tool_events=tool_events, system_chars=system_chars,
        tools_prompt_chars=tools_prompt_chars, calls_log=calls_log,
    )
    sift_service.tool_calls_log.reset(calls_token)

    has_usage = total_usage["total_tokens"] > 0 or total_usage["cost"] > 0
    # reassina URLs de imagem da KB antes de emitir/persistir (token pode ter sido
    # adulterado pelo modelo) — garante que a imagem carregue no chat
    assistant_text = _resign_kb_images(assistant_text)
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

# Detecção de recusa em DUAS camadas, p/ capturar bem sem falso-positivo:
#
# 1) ABERTURAS: frases curtas/ambíguas que só contam como recusa quando a resposta
#    COMEÇA com elas — substring solto marcava "I can't wait to help!" como recusa.
_REFUSAL_OPENERS = (
    "não posso", "nao posso", "não vou", "nao vou", "não poderei", "nao poderei",
    "desculpe, mas", "desculpe mas", "sinto muito, mas", "sinto muito mas",
    "infelizmente não", "infelizmente nao", "não sou capaz", "nao sou capaz",
    "não posso ajudar", "nao posso ajudar", "como uma ia,", "como um modelo de linguagem",
    "i can't", "i cannot", "i can not", "i'm sorry, but", "i am sorry, but",
    "i'm sorry but", "i'm unable", "i am unable", "i won't", "i will not",
    "i'm not able", "i am not able", "i'm afraid i", "i must decline",
    "unfortunately, i can", "unfortunately i can", "as an ai,",
)
# Continuações POSITIVAS logo após uma abertura → veta o falso-positivo
# ("I can't wait to help", "não posso deixar de recomendar").
_REFUSAL_VETO = (
    "wait", "believe how", "thank", "help you enough", "stop thinking",
    "deixar de", "esperar", "acreditar", "agradecer", "conter",
)
# 2) FRASES FORTES: recusa inequívoca em QUALQUER ponto do início da resposta.
_REFUSAL_STRONG = (
    "i must decline", "i'm not comfortable", "i am not comfortable",
    "cannot assist with", "can't assist with", "cannot help with that",
    "i can't help with that", "i cannot help with that", "against my guidelines",
    "against my programming", "not able to provide that", "unable to provide that",
    "não posso ajudar com", "nao posso ajudar com", "não posso te ajudar com",
    "nao posso te ajudar com", "não posso atender", "nao posso atender",
    "vai contra minhas", "não posso continuar", "nao posso continuar",
    "não é apropriado", "nao e apropriado",
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
    # refusal (padrão): recusa no INÍCIO da resposta (não em substring solto)
    low = body.lstrip()[:400].lower()
    if any(p in low for p in _REFUSAL_STRONG):
        return True
    for opener in _REFUSAL_OPENERS:
        if low.startswith(opener):
            after = low[len(opener):len(opener) + 40]
            if not any(v in after for v in _REFUSAL_VETO):
                return True
    return False


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
    for grp in ("input_breakdown", "output_breakdown", "tools_breakdown", "extra_breakdown"):
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
    base_extra = turn_kwargs.get("extra_system")  # extra do canal (ex.: WhatsApp) é preservado
    cur_model, cur_key, cur_base = base_model, base_key, base_base
    extra_system: str | None = None
    merged_usage: dict | None = None
    # fluxo dos guardas p/ a UI: cada acionamento vira uma entrada no tool_events
    # da mensagem final (kind "guard") — o usuário vê o que aconteceu e por quê.
    guard_log: list[dict[str, Any]] = []
    attempt = 0

    base_breakdown = dict(turn_kwargs.get("extra_breakdown") or {})
    while True:
        attempt += 1
        # o extra do canal (base_extra) SEMPRE entra; reforços de guarda são somados
        combined_extra = (
            f"{base_extra}\n\n{extra_system}".strip()
            if base_extra and extra_system
            else (extra_system or base_extra)
        )
        # rotula o reforço do guarda no detalhamento de uso (painel "Extenso")
        combined_breakdown = (
            {**base_breakdown, "guards": len(extra_system)} if extra_system else base_breakdown
        )
        kw = {
            **turn_kwargs,
            "model": cur_model, "api_key": cur_key, "base_url": cur_base,
            "extra_system": combined_extra,
            "extra_breakdown": combined_breakdown or None,
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
            rejected_model = cur_model  # modelo que produziu a resposta rejeitada
            action = hit.get("action") or "reinforce"
            inj = ""
            if action == "fallback_model" and (hit.get("fallback_model") or "").strip():
                cur_model = hit["fallback_model"].strip()
                cur_key = hit.get("_api_key") or base_key
                cur_base = hit.get("_base_url")
            else:  # reinforce
                action = "reinforce"
                inj = (hit.get("inject_text") or "").strip()
                if inj:
                    extra_system = (extra_system + "\n\n" + inj).strip() if extra_system else inj
            guard_log.append({
                "kind": "guard",
                "name": hit.get("name") or "Guarda de saída",
                "data": {
                    "attempt": attempt,
                    "detect": hit.get("detect") or "refusal",
                    "action": action,
                    "model": rejected_model,
                    "fallback_model": cur_model if action == "fallback_model" else None,
                    "injected": inj[:400] or None,
                    "rejected_preview": (text or "").strip()[:320] or None,
                },
            })
            yield {
                "type": "guard",
                "name": hit.get("name") or "Guarda de saída",
                "action": action,
                "attempt": attempt,
                "detect": hit.get("detect") or "refusal",
                "fallback_model": cur_model if action == "fallback_model" else None,
            }
            yield {"type": "guard_reset"}  # o front descarta a tentativa anterior
            continue

        # aceito (ou orçamento esgotado): emite o resultado final
        if final_done is not None:
            if merged_usage is not None:
                final_done = {**final_done, "usage": merged_usage}
            if guard_log:
                # o fluxo dos guardas entra ANTES dos eventos de tool da tentativa
                # final (ordem cronológica) e persiste com a mensagem
                evs = guard_log + list(final_done.get("tool_events") or [])
                final_done = {**final_done, "tool_events": evs or None}
            yield final_done
        elif last_error is not None:
            yield last_error
        return
