"""Mesa-redonda como EQUIPE de agentes (sem banco/HTTP — as rotas injetam o resto).

Cada participante é um agente de verdade: o turno dele passa pela mesma máquina de
um turno normal (tools/SIFT, skills, conhecimento, memória, raciocínio) — ver
roundtable_routes. Aqui ficam as partes puras: o enquadramento do system prompt
(trabalho em equipe sobre o pedido do usuário, sem encenação), a visão do
transcript de cada participante (quem disse o quê), a escolha do moderador, os
marcadores de parada e o laço que alterna os participantes.

Antes a mesa era um "debate" de personas sem ferramentas: os modelos inventavam
dados e só conversavam entre si. As regras abaixo existem para impedir isso.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .turn_setup import _final_message_fields

# marcadores de fim de trabalho que o participante escreve numa linha própria
DONE_MARK = "[[FIM]]"
ASK_MARK = "[[AGUARDANDO_USUARIO]]"
_MARK_RE = re.compile(r"\[\[\s*(FIM|AGUARDANDO[ _]USU[AÁ]RIO)\s*\]\]", re.IGNORECASE)

# transcript enviado ao moderador: só o recente e aparado (ele só decide quem age)
_MOD_MAX_MESSAGES = 30
_MOD_MAX_CHARS = 1500


def speaker_of(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name") or "Modelo",
        "model": p.get("model"),
        "color": p.get("color"),
    }


def build_system(base_system: str | None, role: str | None, self_name: str, others: list[str]) -> str:
    """System do participante: o prompt do próprio modelo + regras da equipe.

    `role` (o antigo campo "persona") vira a FUNÇÃO do agente na equipe — uma divisão
    de trabalho, não um personagem."""
    team = ", ".join(others) if others else "nenhum outro agente"
    frame = (
        f"Você é {self_name}, um agente trabalhando em equipe com: {team}. "
        "Isto não é um debate nem uma encenação: vocês executam JUNTOS o pedido do usuário.\n"
        "Regras:\n"
        "- As mensagens marcadas [Usuário] são as instruções da equipe. Faça o que ele pediu; "
        "não mude o objetivo nem invente tarefas.\n"
        "- Use suas ferramentas para obter dados reais (ler arquivos, rodar comandos, buscar). "
        "NUNCA invente resultados, arquivos, números, código ou saídas de ferramentas. Se você "
        "não tem a ferramenta ou o acesso necessário, diga isso claramente e o que faltaria.\n"
        "- Leia o que os colegas já fizeram e construa em cima: não repita trabalho feito; "
        "se discordar de algo, verifique com dados antes.\n"
        "- Na sua vez, execute uma parte concreta do trabalho. Termine relatando em poucas "
        "linhas o que você fez, o que encontrou (com a evidência) e o que ainda falta.\n"
        "- Seja direto: sem saudações, elogios aos colegas ou falas por eles, e sem prefixar "
        "seu nome. Mensagens marcadas [Nome] são dos outros agentes.\n"
        f"- Quando o pedido do usuário estiver completamente atendido, entregue a resposta "
        f"final consolidada e termine com a linha {DONE_MARK}\n"
        f"- Se a equipe precisar de uma decisão ou informação do usuário para seguir, faça a "
        f"pergunta e termine com a linha {ASK_MARK}"
    )
    parts = [base_system, f"Sua função nesta equipe: {role.strip()}" if role and role.strip() else None, frame]
    return "\n\n".join(p for p in parts if p)


def split_marks(text: str) -> tuple[str, str | None]:
    """Tira os marcadores de parada do texto → (texto limpo, "done" | "ask" | None)."""
    outcome: str | None = None
    for m in _MARK_RE.finditer(text or ""):
        outcome = "done" if m.group(1).upper() == "FIM" else "ask"
    if outcome is None:
        return text, None
    return _MARK_RE.sub("", text).rstrip(), outcome


def tool_names(tool_events: list[dict] | None) -> list[str]:
    """Nomes (únicos, em ordem) das ferramentas chamadas num turno."""
    seen: list[str] = []
    for t in tool_events or []:
        if isinstance(t, dict) and t.get("kind") == "call" and t.get("name") and t["name"] not in seen:
            seen.append(str(t["name"]))
    return seen


def _label(c: dict, names: dict[str | None, str]) -> str:
    if c.get("is_summary"):
        return "[Resumo da conversa anterior]"
    if c["role"] == "user":
        return "[Usuário]"
    return f"[{names.get(c.get('speaker')) or 'Assistente'}]"


def turn_prompt(self_name: str) -> str:
    return (
        f"Sua vez, {self_name}. Continue o trabalho no pedido do usuário a partir do estado "
        "atual: faça a próxima parte concreta (com ferramentas, para dados reais) e relate o "
        "que fez e encontrou."
    )


def participant_view(
    convo: list[dict], names: dict[str | None, str], target_pid: str, self_name: str,
) -> tuple[list[dict], str]:
    """Transcript compartilhado → histórico de UM participante.

    Falas dele = assistant (com o raciocínio replayável, se houver). Falas do usuário e
    dos colegas viram mensagens de usuário ROTULADAS ([Usuário] / [Nome]); as seguidas
    são fundidas numa só (vários provedores exigem alternância). O pedido da vez vai
    como `user_text`, junto do último bloco quando possível."""
    out: list[dict] = []
    chunk: list[str] = []
    media: dict[str, list] = {}

    def flush() -> None:
        if chunk:
            entry: dict[str, Any] = {"role": "user", "content": "\n\n".join(chunk)}
            entry.update({k: list(v) for k, v in media.items()})
            out.append(entry)
        chunk.clear()
        media.clear()

    for c in convo:
        if c["role"] == "assistant" and c.get("speaker") == target_pid and not c.get("is_summary"):
            flush()
            own = dict(c.get("entry") or {"content": c["content"]})
            own["role"] = "assistant"
            own["content"] = c["content"]
            for k in ("_images", "_files"):
                own.pop(k, None)
            out.append(own)
            continue
        body = c.get("content") or ""
        used = c.get("tools") or []
        if used:
            body = f"{body}\n(ferramentas usadas: {', '.join(used[:10])})"
        chunk.append(f"{_label(c, names)}\n{body}".strip())
        for k in ("_images", "_files"):
            v = (c.get("entry") or {}).get(k)
            if v:
                media.setdefault(k, []).extend(v)
    flush()

    nudge = turn_prompt(self_name)
    if out and out[-1]["role"] == "user" and not any(k in out[-1] for k in ("_images", "_files")):
        last = out.pop()
        return out, f"{last['content']}\n\n{nudge}"
    return out, nudge


def next_rr(order: list[str], last: str | None) -> str:
    if last in order:
        return order[(order.index(last) + 1) % len(order)]
    return order[0]


def moderator_prompt(
    parts: list[dict], convo: list[dict], names: dict[str | None, str],
) -> tuple[str, str]:
    """(system, transcript) do moderador. `parts`: [{id, name, role}] na ordem da mesa."""
    roster = "\n".join(
        f"{i}. {p['name']}" + (f" — função: {p['role']}" if p.get("role") else "")
        for i, p in enumerate(parts, 1)
    )
    system = (
        "Você coordena uma equipe de agentes que executa o pedido do usuário. Participantes:\n"
        f"{roster}\n\n"
        "Leia a conversa e decida quem deve agir AGORA para avançar o pedido do usuário. "
        "Responda APENAS com o número do participante, ou FIM se o pedido já foi atendido, "
        "se a equipe precisa de uma resposta do usuário, ou se estão só conversando sem avançar."
    )
    recent = convo[-_MOD_MAX_MESSAGES:]
    lines = []
    for c in recent:
        body = (c.get("content") or "").strip()
        if len(body) > _MOD_MAX_CHARS:
            body = body[:_MOD_MAX_CHARS] + " […]"
        used = c.get("tools") or []
        note = f"\n(ferramentas usadas: {', '.join(used[:10])})" if used else ""
        lines.append(f"{_label(c, names)}\n{body}{note}")
    return system, "\n\n".join(lines) or "(a conversa ainda não começou)"


def parse_moderator(answer: str, parts: list[dict]) -> str | None:
    """Resposta do moderador → id do participante, "STOP" ou None (fallback)."""
    a = (answer or "").strip()
    low = a.lower()
    if re.match(r"^\W*(fim|stop)\b", low):
        return "STOP"
    m = re.search(r"\b(\d{1,2})\b", low)
    if m and 1 <= int(m.group(1)) <= len(parts):
        return parts[int(m.group(1)) - 1]["id"]
    for p in sorted(parts, key=lambda x: -len(x.get("name") or "")):
        nm = (p.get("name") or "").strip().lower()
        if nm and nm in low:
            return p["id"]
    if re.search(r"\b(fim|stop)\b", low):
        return "STOP"
    return None


# --------------------------------------------------------------------------- #
# Laço da mesa
# --------------------------------------------------------------------------- #
TurnFn = Callable[[str, list[dict], str], AsyncIterator[dict]]
# persist(pid, speaker, content, reasoning, collected) -> {"message_id", "content", "extra": [eventos]} | None
PersistFn = Callable[[str, dict, str, dict | None, dict], Awaitable[dict | None]]


def _new_collected() -> dict:
    return {
        "content": "", "usage": None, "reasoning": None, "tools": None, "memories": None,
        "streamed": "", "reasoning_streamed": "", "tools_streamed": [], "error": None,
    }


def _absorb(ev: dict, col: dict) -> None:
    """Acumula o estado de um turno (mesmo critério do driver de geração)."""
    t = ev.get("type")
    if t == "done":
        col["content"] = ev.get("content", "")
        col["usage"] = ev.get("usage")
        col["reasoning"] = ev.get("reasoning")
        col["tools"] = ev.get("tool_events")
        col["memories"] = ev.get("memories")
    elif t == "token":
        col["streamed"] += ev.get("text", "")
    elif t == "reasoning":
        col["reasoning_streamed"] += ev.get("text", "")
    elif t == "tool_call":
        col["tools_streamed"].append({"kind": "call", "name": ev.get("name"), "data": ev.get("arguments")})
    elif t == "tool_result":
        col["tools_streamed"].append({"kind": "result", "name": ev.get("name"), "data": ev.get("result")})
    elif t == "error":
        col["error"] = ev.get("message")
    if t in {"tool_call", "guard_reset"}:
        col["streamed"] = ""


async def run_loop(
    *,
    order: list[str],
    speakers: dict[str, dict],
    names: dict[str | None, str],
    convo: list[dict],
    turn: TurnFn,
    persist: PersistFn,
    policy: str = "round_robin",
    cap: int = 6,
    one_step: bool = False,
    explicit_next: str | None = None,
    last_pid: str | None = None,
    pick: Callable[[list[dict]], Awaitable[str | None]] | None = None,
    drain_user: Callable[[], list[str]] | None = None,
) -> AsyncIterator[dict]:
    """Alterna os participantes até: alguém marcar FIM/pergunta ao usuário, o moderador
    encerrar, o teto de turnos, turnos seguidos sem saída, ou um só passo (one_step).
    Mensagens do usuário que chegam no meio (já persistidas) entram no transcript e
    renovam o orçamento — são uma nova instrução para a equipe."""
    lp = last_pid
    turns = 0
    empty = 0

    def take_user() -> bool:
        new = drain_user() if drain_user else []
        for text in new:
            convo.append({"role": "user", "content": text, "speaker": None})
        return bool(new)

    while True:
        if take_user():
            turns, empty = 0, 0
        if turns >= cap:
            yield {"type": "roundtable_done", "reason": "limit"}
            return
        pid: str | None = None
        first_explicit = turns == 0 and explicit_next in speakers
        if policy == "moderator" and pick is not None and not first_explicit:
            pid = await pick(convo)
            if pid == "STOP":
                if take_user():
                    turns, empty = 0, 0
                    continue
                yield {"type": "roundtable_done", "reason": "moderator"}
                return
        if pid not in speakers:
            pid = explicit_next if first_explicit else next_rr(order, lp)
        sp = speakers[pid]
        sid = sp["id"]
        name = names.get(pid) or "Modelo"
        yield {"type": "speaker_start", "speaker": sp}
        history, user_text = participant_view(convo, names, pid, name)
        col = _new_collected()
        try:
            async for ev in turn(pid, history, user_text):
                _absorb(ev, col)
                if ev.get("type") == "done":
                    continue  # o fim da fala é o speaker_end (o "done" encerraria a UI)
                yield {**ev, "speaker": sid}
        except asyncio.CancelledError:
            # Pausa/Parar: salva o parcial desta fala (texto + ferramentas já usadas)
            if not col["tools"] and col["tools_streamed"]:
                col["tools"] = col["tools_streamed"]
            content, reasoning = _final_message_fields(col)
            content, _ = split_marks(content)
            if content or col["tools"]:
                await asyncio.shield(persist(pid, sp, content, reasoning, col))
            raise
        except Exception as exc:  # noqa: BLE001 - a falha de um agente não derruba a mesa
            col["error"] = str(exc)
            yield {"type": "error", "message": str(exc), "speaker": sid}
        if not col["tools"] and col["tools_streamed"]:
            col["tools"] = col["tools_streamed"]
        real = (col["content"] or col["streamed"]).strip()
        content, reasoning = _final_message_fields(col)
        content, outcome = split_marks(content)
        saved = await persist(pid, sp, content, reasoning, col) if (content or col["tools"]) else None
        turns += 1
        lp = pid
        used = tool_names(col["tools"])
        if saved:
            for extra in saved.get("extra") or []:
                yield extra
        if real:
            empty = 0
            clean = saved.get("content", content) if saved else content
            convo.append({"role": "assistant", "content": clean, "speaker": pid, "tools": used})
        else:
            empty += 1
        yield {
            "type": "speaker_end", "speaker": sp,
            "message_id": saved.get("message_id") if saved else None,
            "content": saved.get("content", content) if saved else content,
            "tool_events": col["tools"] or None,
            "outcome": outcome,
        }
        stop_reason = (
            outcome
            or ("stalled" if empty >= len(order) else None)
            or ("step" if one_step else None)
        )
        if stop_reason:
            # o usuário escreveu enquanto o agente falava: é instrução nova, segue
            if take_user():
                turns, empty = 0, 0
                continue
            yield {"type": "roundtable_done", "reason": stop_reason}
            return
