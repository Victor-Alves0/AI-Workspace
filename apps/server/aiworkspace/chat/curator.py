"""Aprendizado Proativo — o "Curator" (revisão em background estilo Hermes).

A cada N turnos (ou quando um turno dá sinais fortes: muitas tools / erro recuperado
/ correção do usuário), forka em BACKGROUND uma revisão barata da janela recente e:
  - propõe SKILLS (workflow reutilizável) → linhas `skill_proposals` (pendentes)
  - consolida MEMÓRIA (fatos duráveis do usuário) → mem0 PENDENTE (add_manual pending)

NUNCA salva sozinho: tudo entra para aprovação (skills na aba Skills, memórias no
MemoryView). Opt-in: só roda se `profile["learning"].enabled`. Best-effort — nunca
quebra o turno. Reusa o padrão de `_spawn_memory_write` (orchestrator).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from ..db import SessionLocal
from ..memory import mem0_service
from ..models import Message, SkillProposal, User
from ..providers import openrouter

logger = logging.getLogger(__name__)

_bg_tasks: set[asyncio.Task] = set()

# sinal forte (item #2): baixa o intervalo efetivo p/ revisar já quando o turno
# rende material p/ skill (workflow multi-tool, erro recuperado, correção do usuário)
_STRONG_TOOLS = 5
_STRONG_EFF = 5  # intervalo efetivo quando há sinal forte

# frases de correção do usuário → sinal de "isso vira regra/skill"
_CORRECTION_RE = re.compile(
    r"^\s*(na verdade|na real|não[,.]|nao[,.]|isso (está|esta) errado|"
    r"errado|corrig|actually|no[,.]|that's wrong|thats wrong|not quite)",
    re.IGNORECASE,
)

_SYSTEM = (
    "You are a background reviewer that helps an AI assistant learn from a conversation. "
    "Read the recent transcript and extract ONLY what is genuinely worth persisting. "
    "Reply with STRICT JSON (no prose, no code fences) of the shape: "
    '{"memories":[{"text":"...","scope":"global|model|chat"}],'
    '"skills":[{"name":"...","description":"...","content":"...","tags":["..."]}]}\n'
    "Rules:\n"
    "- memories: durable facts/preferences ABOUT THE USER worth remembering across chats "
    "(their conventions, projects, preferences, recurring context). One short fact each. "
    "Default scope 'global'. Empty list if nothing durable.\n"
    "- skills: propose a skill ONLY when the conversation shows a REUSABLE workflow worth "
    "codifying — a multi-step tool workflow that worked, a correction the user made that "
    "should become a rule, or recovery from a non-obvious error. 'content' is a concise "
    "how-to in markdown (the steps). Be conservative: NO skill for trivial one-step tasks. "
    "Empty list if nothing is worth it.\n"
    "- Write in the user's language. Output ONLY the JSON object."
)


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower().strip()).strip("_")
    return s[:64] or "skill"


# corpo mínimo p/ uma skill valer a pena — barra propostas-lixo (ex.: content "d"),
# que aconteciam quando o modelo de revisão devolvia placeholder em vez de passos reais.
_MIN_SKILL_CHARS = 40
_MIN_SKILL_WORDS = 8


def _useful_skill(name: str, content: str) -> bool:
    c = (content or "").strip()
    return len(c) >= _MIN_SKILL_CHARS and len(c.split()) >= _MIN_SKILL_WORDS and len((name or "").strip()) >= 3


def turn_signals(user_text: str, tool_events: list[dict] | None) -> dict[str, Any]:
    """Deriva os sinais do turno (para o gatilho heurístico + contexto da revisão)."""
    evs = tool_events or []
    calls = sum(1 for e in evs if isinstance(e, dict) and e.get("kind") == "call")
    had_error = any(
        isinstance(e, dict) and e.get("kind") == "guard" for e in evs
    ) or any(
        isinstance(e, dict) and e.get("kind") == "result"
        and "error" in str(e.get("data") or "").lower()[:200]
        for e in evs
    )
    corrected = bool(_CORRECTION_RE.search((user_text or "")[:120]))
    return {"tool_calls": calls, "had_error": had_error, "user_corrected": corrected}


def _is_strong(signals: dict[str, Any]) -> bool:
    return (
        int(signals.get("tool_calls") or 0) >= _STRONG_TOOLS
        or bool(signals.get("had_error"))
        or bool(signals.get("user_corrected"))
    )


def _parse_review(raw: str) -> dict[str, list]:
    """Extrai {memories, skills} do JSON do modelo, tolerante a cercas/ruído."""
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\n?|\n?```$", "", txt).strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i == -1 or j == -1:
        return {"memories": [], "skills": []}
    try:
        data = json.loads(txt[i : j + 1])
    except (ValueError, TypeError):
        return {"memories": [], "skills": []}
    mems, skills = [], []
    for m in data.get("memories") or []:
        if isinstance(m, dict) and str(m.get("text") or "").strip():
            scope = m.get("scope") if m.get("scope") in ("global", "model", "chat") else "global"
            mems.append({"text": str(m["text"]).strip()[:500], "scope": scope})
    for s in data.get("skills") or []:
        if isinstance(s, dict) and _useful_skill(str(s.get("name") or ""), str(s.get("content") or "")):
            tags = [str(t).strip().lower()[:40] for t in (s.get("tags") or []) if str(t).strip()][:10]
            skills.append({
                "name": str(s["name"]).strip()[:255],
                "description": str(s.get("description") or "").strip()[:2000],
                "content": str(s["content"]).strip()[:200_000],
                "tags": tags,
            })
    return {"memories": mems[:8], "skills": skills[:4]}


async def _load_settings(user_id: uuid.UUID) -> dict[str, Any] | None:
    async with SessionLocal() as db:
        u = await db.get(User, user_id)
        if u is None:
            return None
        cfg = {"enabled": False, "interval": 10, "model": ""}
        cfg.update((u.profile or {}).get("learning") or {})
        return cfg


async def _assistant_count(chat_id: uuid.UUID) -> int:
    async with SessionLocal() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(Message).where(
                    Message.chat_id == chat_id, Message.role == "assistant"
                )
            ) or 0
        )


async def _recent_window(chat_id: uuid.UUID, turns: int) -> str:
    """Transcrição das últimas ~`turns` trocas (user/assistant), cronológica."""
    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc())
            .limit(max(2, turns * 2))
        ))
    rows.reverse()
    lines = []
    for m in rows:
        who = "User" if m.role == "user" else "Assistant"
        lines.append(f"{who}: {(m.content or '').strip()[:800]}")
    return "\n\n".join(lines)


async def _review(
    api_key: str, user_id: str, chat_id: str, agent_id: str | None,
    model: str, base_url: str | None, signals: dict[str, Any],
) -> None:
    # colunas chat_id/user_id são UUID; o orchestrator passa str → coage p/ o ORM.
    # mem0 continua recebendo str (chat_id/user_id string).
    try:
        uid = uuid.UUID(str(user_id))
        cid = uuid.UUID(str(chat_id))
    except (ValueError, TypeError):
        return

    settings = await _load_settings(uid)
    if not settings or not settings.get("enabled"):
        return
    interval = max(4, int(settings.get("interval") or 10))
    strong = _is_strong(signals)
    eff = min(interval, _STRONG_EFF) if strong else interval
    # +1: o turno recém-concluído pode ainda não estar persistido quando isto roda
    count = (await _assistant_count(cid)) + 1
    if eff <= 0 or count % eff != 0:
        return

    review_model = (settings.get("model") or "").strip() or model
    if not review_model:
        return
    window = await _recent_window(cid, interval)
    if not window.strip():
        return

    hint = ""
    if strong:
        bits = []
        if signals.get("tool_calls"):
            bits.append(f"{signals['tool_calls']} tool calls")
        if signals.get("had_error"):
            bits.append("an error was recovered")
        if signals.get("user_corrected"):
            bits.append("the user corrected the assistant")
        if bits:
            hint = f"\n\n(Signals this turn: {', '.join(bits)} — consider a skill.)"

    try:
        out = await openrouter.complete(
            api_key,
            review_model,
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Recent conversation:\n\n{window}{hint}"},
            ],
            params={"max_tokens": 1200, "temperature": 0.2},
            timeout=60.0,
            base_url=base_url,
        )
    except Exception as exc:  # noqa: BLE001 - revisão é best-effort
        logger.info("curator: revisão falhou (chat %s): %s", chat_id, exc)
        return

    review = _parse_review(out)

    # memórias curadas → PENDENTES (add_manual pending; sem 2ª extração LLM)
    for mem in review["memories"]:
        scope = mem["scope"]
        aid = agent_id if scope == "model" else None
        if scope == "model" and not aid:
            scope = "global"  # sem agente → cai p/ global
        try:
            await run_in_threadpool(
                lambda text=mem["text"], sc=scope, a=aid: mem0_service.add_manual(
                    api_key, user_id, text, scope=sc, chat_id=chat_id, agent_id=a, pending=True
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("curator: falha ao gravar memória pendente")

    # skills → PROPOSTAS pendentes (nunca cria Skill direto)
    if review["skills"]:
        async with SessionLocal() as db:
            for sk in review["skills"]:
                db.add(SkillProposal(
                    user_id=uid,
                    chat_id=cid,
                    name=sk["name"],
                    slug=_slugify(sk["name"]),
                    description=sk["description"],
                    content=sk["content"],
                    tags=sk["tags"],
                    source="curator",
                    status="pending",
                ))
            await db.commit()
    logger.info(
        "curator: chat %s revisado (%d memórias, %d skills propostas)",
        chat_id, len(review["memories"]), len(review["skills"]),
    )


def maybe_review(
    api_key: str, user_id: str, chat_id: str | None, agent_id: str | None,
    model: str, base_url: str | None, signals: dict[str, Any],
) -> None:
    """Agenda a revisão em background (não bloqueia o `done`). No-op sem chat/chave."""
    if not chat_id or not api_key:
        return
    task = asyncio.create_task(
        _review(api_key, user_id, chat_id, agent_id, model, base_url, signals)
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
