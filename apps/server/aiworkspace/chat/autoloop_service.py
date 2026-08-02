"""Loop autônomo (self-continue): a IA continua a tarefa SOZINHA, turno após turno,
sem o usuário digitar "continue" — até concluir o objetivo, bater um teto, ou precisar
de uma decisão dele.

Motivação (harness): hoje é 1 mensagem do usuário → 1 turno → PARA. No chat do Metabase o
usuário empurrou ~8 vezes ("continue", "prossiga", "faça isso"). Este serviço fecha esse
atrito: quando um turno termina e o Ledger da tarefa ainda tem passo pendente, dispara o
próximo turno automaticamente (reusando `resume.resume_chat_turn`). É um MULTIPLICADOR —
amplifica acerto E erro — então só roda com fundamentação sólida (Ledger + guarda-juiz) e
com FREIOS explícitos. Ver [[harness-ceiling-and-grounding-guard]].

Opt-in por-modelo (capability `autonomous_loop`) e só em chat de projeto (Codespace) com
objetivo no Ledger. Estado em memória por-processo (como generation/exec_jobs) — um restart
simplesmente para o loop.

Paradas (nesta ordem):
  - card `ask` pendente (o modelo perguntou/pediu confirmação) → controle é do usuário;
  - erro no turno;
  - objetivo do Ledger `done`/`paused` ou sem próximo passo → concluído (parada natural);
  - `halt` (o usuário apertou Parar) → interrompe o loop;
  - teto de iterações automáticas (config, default 6) → pausa e avisa;
  - convergência: N turnos sem mudança no Ledger (evita repetição) → pausa e avisa;
  - teto de custo por-tarefa (opt-in) → pausa e avisa;
  - orçamento global bloqueado → o próprio `resume_chat_turn` já barra.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import uuid as _uuid

from ..config import get_settings

logger = logging.getLogger(__name__)


class _Run:
    """Estado de uma corrida autônoma (desde a última mensagem do usuário)."""

    def __init__(self) -> None:
        self.iterations = 0        # continuações automáticas já disparadas nesta corrida
        self.cost = 0.0            # custo US$ acumulado na corrida
        self.last_fp: str | None = None   # fingerprint do Ledger no turno anterior
        self.stalls = 0            # turnos consecutivos sem mudança no Ledger
        self.halted = False        # o usuário apertou Parar → não continuar


_runs: dict[str, _Run] = {}
_lock = threading.Lock()


def _run(chat_id: str) -> _Run:
    with _lock:
        r = _runs.get(chat_id)
        if r is None:
            r = _Run()
            _runs[chat_id] = r
        return r


def reset(chat_id: str) -> None:
    """Mensagem NOVA do usuário = tarefa nova: zera o contador do loop."""
    if not chat_id:
        return
    with _lock:
        _runs.pop(str(chat_id), None)


def halt(chat_id: str) -> None:
    """Usuário apertou Parar: interrompe o loop autônomo desta corrida."""
    if not chat_id:
        return
    _run(str(chat_id)).halted = True


def is_enabled(model_config) -> bool:
    caps = getattr(model_config, "capabilities", None) or {}
    return bool(caps.get("autonomous_loop"))


def _cfg(model_config) -> tuple[int, float, int]:
    """(max_iterations, cost_cap_usd, stall_limit) — default global + override por-modelo."""
    fc = (getattr(model_config, "filter_config", None) or {}).get("autoloop") or {}
    s = get_settings()
    max_iter = int(fc.get("max_iterations") or s.autoloop_max_iterations)
    cap_raw = fc.get("cost_cap_usd")
    cost_cap = float(cap_raw if cap_raw is not None else s.autoloop_task_cost_cap_usd)
    return max(1, max_iter), max(0.0, cost_cap), max(1, int(s.autoloop_stall_limit))


def _ledger_fingerprint(led: dict | None) -> str:
    """Hash do estado do Ledger que importa p/ progresso: status, próximo passo, e o
    (id,status) de cada passo/achado. Igual em 2 turnos = não houve progresso."""
    if not led:
        return ""
    parts = [led.get("status") or "", (led.get("next_step") or "").strip()]
    for s in led.get("plan") or []:
        parts.append(f"p:{s.get('id')}:{s.get('status')}")
    for f in led.get("findings") or []:
        parts.append(f"f:{f.get('id')}:{f.get('status')}")
    return hashlib.sha1("|".join(parts).encode("utf-8", "ignore")).hexdigest()


def _find_ask(obj) -> bool:
    """Varre a estrutura de tool_events por um card kind=='ask' (pergunta/confirmação
    pendente) — sinal de que o modelo está esperando o usuário."""
    if isinstance(obj, dict):
        if obj.get("kind") == "ask":
            return True
        return any(_find_ask(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_find_ask(x) for x in obj)
    return False


def _continue_note(i: int, max_iter: int) -> str:
    return (
        f"[Continuação autônoma {i}/{max_iter} — sem intervenção do usuário]\n"
        "Prossiga a tarefa seguindo o OBJETIVO do Ledger; avance o próximo passo pendente e "
        "mantenha o Ledger atualizado (passos/achados) — é assim que o loop sabe que há "
        "progresso. NÃO espere o usuário.\n"
        "- Se CONCLUIU o objetivo: marque o Ledger com `task.ledger.track action=set "
        "status=done` e finalize dizendo que terminou.\n"
        "- Se precisar de uma DECISÃO do usuário (ambiguidade real, escolha de rumo, ação "
        "irreversível): pergunte com `ask_options` e PARE.\n"
        "- Se não há mais nada a fazer: diga que terminou — não invente trabalho."
    )


async def _notify_pause(chat_id: str, user_id: str, reason: str) -> None:
    """Avisa o usuário (in-app + push) que o loop pausou com trabalho pendente."""
    from ..db import SessionLocal
    from ..models import Notification
    title = "Loop automático pausado"
    body = f"{reason}. Digite 'continuar' para seguir."
    try:
        async with SessionLocal() as s:
            s.add(Notification(user_id=_uuid.UUID(str(user_id)), title=title,
                               body=body[:500], chat_id=_uuid.UUID(str(chat_id))))
            await s.commit()
        from ..push_service import send_to_user
        asyncio.create_task(send_to_user(user_id, title, body, "/"))
    except Exception:  # noqa: BLE001 - aviso é best-effort
        logger.exception("autoloop: falha ao notificar pausa (chat %s)", chat_id)


async def _continue_when_idle(chat_id: str, note: str) -> None:
    """Espera o chat ficar ocioso e dispara o próximo turno. O `_finish` desse turno
    chama `after_turn` de novo → o loop encadeia até uma condição de parada."""
    from . import generation, resume
    for _ in range(180):  # ~180 * 1s: o turno atual pode ainda estar redigindo o fim
        gen = generation.get_active(chat_id)
        if gen is None or gen.done:
            break
        await asyncio.sleep(1.0)
    # re-checa o halt (o usuário pode ter apertado Parar nesse meio tempo)
    if _run(chat_id).halted:
        return
    await resume.resume_chat_turn(
        chat_id, note,
        notify_title="Continuação autônoma",
        notify_body="A IA continuou a tarefa sozinha.",
    )


async def after_turn(*, chat_id: str | None, user_id: str, project_id: str | None,
                     model_config, collected: dict | None) -> None:
    """Chamado no fim de CADA turno (envio do usuário e continuação). Decide se
    auto-continua e agenda. Nunca levanta (roda dentro do _finish, blindado)."""
    try:
        if not chat_id or not project_id or not is_enabled(model_config):
            return
        chat_id = str(chat_id)
        max_iter, cost_cap, stall_limit = _cfg(model_config)
        r = _run(chat_id)
        if r.halted:
            return

        # custo do turno acumula na corrida
        usage = (collected or {}).get("usage") or {}
        try:
            r.cost += float(usage.get("cost") or 0.0)
        except (TypeError, ValueError):
            pass

        # o modelo está esperando o usuário (card ask/confirmação) → controle é dele
        if _find_ask((collected or {}).get("tools")):
            return
        # erro no turno → para
        if (collected or {}).get("error"):
            return

        # o Ledger é a fonte da verdade do "acabou?"
        from . import ledger_service
        led = await ledger_service.load(chat_id)
        if not led:
            return  # sem objetivo → não é tarefa dirigida por Ledger; não auto-continua

        # convergência: o Ledger mudou desde o último turno?
        fp = _ledger_fingerprint(led)
        r.stalls = 0 if (r.last_fp is None or fp != r.last_fp) else (r.stalls + 1)
        r.last_fp = fp

        status = led.get("status") or "active"
        has_next = bool((led.get("next_step") or "").strip()) or any(
            (s.get("status") in ("todo", "doing")) for s in (led.get("plan") or []))

        # parada NATURAL (concluído) — sem aviso, o modelo já se despediu
        if status != "active" or not has_next:
            return

        # freios que merecem aviso (paramos, mas há trabalho pendente)
        reason: str | None = None
        if r.iterations >= max_iter:
            reason = f"atingi o teto de {max_iter} passos automáticos"
        elif r.stalls >= stall_limit:
            reason = "não houve progresso no Ledger nos últimos passos (evitando repetição)"
        elif cost_cap > 0 and r.cost >= cost_cap:
            reason = f"atingi o teto de custo desta tarefa (US$ {cost_cap:.2f})"
        if reason:
            await _notify_pause(chat_id, user_id, reason)
            return

        # tudo certo → agenda a continuação (o orçamento global é barrado no resume)
        r.iterations += 1
        asyncio.create_task(_continue_when_idle(chat_id, _continue_note(r.iterations, max_iter)))
    except Exception:  # noqa: BLE001 - o loop nunca derruba a persistência do turno
        logger.exception("autoloop.after_turn falhou (chat %s)", chat_id)
