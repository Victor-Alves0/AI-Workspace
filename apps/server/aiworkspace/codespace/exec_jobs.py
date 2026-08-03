"""Execução em BACKGROUND de comandos do Codespace (downloads/instalações/builds longos).

Motivação: `exec_service.run_command` é síncrono com timeout rígido — um `mvn`/`mise install`
que baixa dependências grandes estoura o timeout e é morto, travando o turno. Aqui o comando
roda DESACOPLADO: a tool volta na hora com um `job_id`; o agente pode ESPERAR inline
(`code.exec.jobs wait`, teto ~20min) OU encerrar o turno e ser ACORDADO num turno novo quando
o job terminar (via `resume_chat_turn`), como o Codex/Claude Code.

Modelo de concorrência (thread-safe p/ tools da SIFT, que rodam em threadpool):
- `start_job` (SÍNCRONO): sobe o subprocesso via `exec_service.spawn_host` + uma thread leitora
  (`proc.communicate()` até EOF — evita deadlock de buffer de pipe em builds verborrágicos).
  Ao fim, a thread grava saída/exit e seta um `threading.Event`. Não toca o event loop.
- `wait_job` (ASYNC): aguarda o Event via `run_in_executor` (não bloqueia o loop).
- `_reaper` (task ASYNC, subida no lifespan): varre os jobs concluídos e, se ninguém esperou e
  o chat não tem geração ativa, dispara o WAKE no loop principal.

Registro em memória por-processo (como `generation._active`): não sobrevive a restart — o
subprocesso também não sobreviveria. Escopo v1: caminho HOST (o `runner` HTTP segue síncrono).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.concurrency import run_in_threadpool

from ..config import get_settings
from . import exec_service

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Persistência (durabilidade): espelha o ciclo de vida em disco SÓ p/ a recuperação
# no boot. Sync psycopg2, fire-and-forget, NUNCA levanta — um erro de persistência
# jamais pode derrubar a execução do job. Ver models/exec_job.py.
# --------------------------------------------------------------------------- #
def _pg():
    import psycopg2
    url = urlparse(get_settings().sync_database_url)
    return psycopg2.connect(
        dbname=url.path.lstrip("/"), user=url.username, password=url.password,
        host=url.hostname, port=url.port or 5432, connect_timeout=10,
    )


def _db_insert(job: "Job") -> None:
    try:
        conn = _pg()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO codespace_exec_jobs (id, job_key, user_id, chat_id, "
                "project_id, worktree, command, status, settled, output_tail, "
                "created_at, updated_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,'running',false,'', now(), now())",
                (str(uuid.uuid4()), job.id, job.user_id, job.chat_id or None,
                 job.project_id or None, job.worktree, job.command[:20000]),
            )
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("exec_jobs._db_insert falhou (%s): %s", job.id, exc)


def _db_update(job_key: str, **fields: Any) -> None:
    if not fields:
        return
    try:
        cols = ", ".join(f"{k} = %s" for k in fields)
        conn = _pg()
        with conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE codespace_exec_jobs SET {cols}, updated_at = now() WHERE job_key = %s",
                (*fields.values(), job_key),
            )
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("exec_jobs._db_update falhou (%s): %s", job_key, exc)


class Job:
    """Um comando rodando (ou concluído) em background."""

    def __init__(self, *, chat_id: str | None, user_id: str, project_id: str,
                 worktree: str | None, command: str):
        self.id = uuid.uuid4().hex[:12]
        self.chat_id = chat_id
        self.user_id = user_id
        self.project_id = project_id
        self.worktree = worktree
        self.command = command
        self.status = "running"          # running | done | failed | killed
        self.exit_code: int | None = None
        self.output = ""
        self.truncated = False
        self.started_at = time.monotonic()
        self.ended_at: float | None = None
        self.proc = None                  # subprocess.Popen
        self._evt = threading.Event()     # setado pela thread leitora ao concluir
        self.waited = False               # algum wait_job já consumiu o resultado?
        self.dispatched = False           # o wake já foi disparado/decidido?
        self.timed_out = False            # o watchdog matou por exceder o wall-clock?

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def seconds(self) -> float:
        return round((self.ended_at or time.monotonic()) - self.started_at, 2)

    def snapshot(self, *, tail: int = 4000) -> dict[str, Any]:
        out = self.output[-tail:] if tail and len(self.output) > tail else self.output
        return {
            "job_id": self.id,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "seconds": self.seconds,
            "output": out,
            "truncated": self.truncated or (tail and len(self.output) > tail) or False,
            "running": self.running,
            "timed_out": self.timed_out,
        }


_jobs: dict[str, Job] = {}
_reaper_task: asyncio.Task | None = None


def _watchdog(job: Job, max_seconds: float) -> None:
    """Mata a árvore do job se ele passar do teto de wall-clock. O background não tem o
    kill de timeout do run síncrono nem (por padrão) RLIMIT_CPU — sem isto, um comando
    travado rodaria para sempre. `_read_thread` então observa o processo morto e conclui."""
    if job._evt.wait(max_seconds):
        return  # terminou sozinho antes do teto
    if job.running and job.proc is not None:
        job.timed_out = True
        try:
            exec_service.kill_tree(job.proc)
        except Exception:  # noqa: BLE001
            pass


def _read_thread(job: Job) -> None:
    """Roda na thread leitora: drena stdout/stderr até EOF, grava resultado, sinaliza."""
    try:
        out, _ = job.proc.communicate()
    except Exception as exc:  # noqa: BLE001
        out = f"[erro lendo a saída: {exc}]"
    text, truncated = exec_service.cap_output(out)
    job.output = text
    job.truncated = truncated
    rc = job.proc.returncode
    job.exit_code = rc
    if job.timed_out:
        job.status = "killed"
        if job.output:
            job.output += "\n[job encerrado pelo watchdog: excedeu o tempo máximo de background]"
    else:
        job.status = "done" if rc == 0 else ("killed" if rc is not None and rc < 0 else "failed")
    job.ended_at = time.monotonic()
    _db_update(job.id, status=job.status, exit_code=job.exit_code,
               output_tail=job.output[-4000:])
    job._evt.set()


def start_job(root: Path, command: str, *, chat_id: str | None, user_id: str,
              project_id: str, worktree: str | None,
              env_extra: dict | None = None) -> dict[str, Any]:
    """Sobe `command` em background. Devolve o cartão `job_started` (ou {error})."""
    command = (command or "").strip()
    if not command:
        return {"error": "comando vazio"}
    job = Job(chat_id=chat_id, user_id=user_id, project_id=project_id,
              worktree=worktree, command=command)
    s = get_settings()
    try:
        # background = trabalho longo por definição → CPU generoso/ilimitado (o síncrono
        # tem o teto apertado). O runaway é contido pelo watchdog de wall-clock.
        job.proc = exec_service.spawn_host(
            command, Path(root), env_extra, cpu_seconds=int(s.code_exec_bg_cpu_seconds))
    except (OSError, ValueError) as exc:
        return {"error": f"não consegui iniciar o comando: {exc}"}
    _jobs[job.id] = job
    _db_insert(job)  # durabilidade: registra o job (running) p/ a recuperação no boot
    threading.Thread(target=_read_thread, args=(job,), daemon=True).start()
    max_wall = float(s.code_exec_bg_max_seconds)
    if max_wall > 0:
        threading.Thread(target=_watchdog, args=(job, max_wall), daemon=True).start()
    return {
        "kind": "job_started",
        "job_id": job.id,
        "command": command,
        "note": (
            "Rodando em background. Para ESPERAR o resultado agora, chame code.exec.jobs "
            "action=wait job_id=" + job.id + " (posso esperar alguns minutos). Ou encerre a "
            "resposta: eu te aviso e continuo sozinho assim que terminar."
        ),
    }


async def wait_job(job_id: str, timeout: float | None = None) -> dict[str, Any]:
    """Aguarda o job concluir (até um teto), inline no turno. Se estourar o teto, devolve
    o status parcial `running` — o job segue vivo e ainda pode acordar um turno."""
    job = _jobs.get(job_id)
    if job is None:
        return {"error": f"job '{job_id}' não encontrado (pode ter expirado)"}
    ceiling = float(get_settings().code_exec_bg_wait_ceiling_seconds)
    wait_for = ceiling if timeout is None else min(float(timeout), ceiling)
    if not job._evt.is_set():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, job._evt.wait, wait_for)
    job.waited = job._evt.is_set() or job.waited
    if not job._evt.is_set():
        snap = job.snapshot()
        snap["note"] = "Ainda rodando após a espera. Encerre a resposta que eu te aviso ao terminar."
        return snap
    # consumido inline (o turno tem o resultado) → SETTLED: nada devendo p/ a recuperação
    _db_update(job.id, settled=True)
    return job.snapshot()


def job_status(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        return {"error": f"job '{job_id}' não encontrado (pode ter expirado)"}
    return job.snapshot()


def list_jobs(chat_id: str | None = None, project_id: str | None = None) -> list[dict[str, Any]]:
    out = []
    for job in _jobs.values():
        if chat_id and job.chat_id != chat_id:
            continue
        if project_id and job.project_id != project_id:
            continue
        out.append(job.snapshot(tail=400))
    return sorted(out, key=lambda j: j["running"], reverse=True)


# --------------------------------------------------------------------------- #
# Reaper: dispara o WAKE quando um job termina sem ninguém esperando
# --------------------------------------------------------------------------- #

async def _fire_wake(job: Job) -> None:
    """Continua o chat num turno novo com o resultado do job (best-effort)."""
    from ..chat import resume  # lazy: evita import circular (resume usa muita coisa do chat)
    status_pt = {"done": "terminou com sucesso", "failed": "terminou com erro",
                 "killed": "foi encerrado"}.get(job.status, "terminou")
    tail = job.output[-6000:] if len(job.output) > 6000 else job.output
    note = (
        f"[Comando em background concluído] `{job.command}` {status_pt} "
        f"(exit {job.exit_code}, {job.seconds}s).\n\nSaída (final):\n```\n{tail}\n```\n\n"
        "Continue a tarefa de onde parou, usando este resultado."
    )
    await resume.resume_chat_turn(
        job.chat_id, note,
        notify_title="Comando em background concluído",
        notify_body=f"{job.command} — exit {job.exit_code}",
    )


async def _maybe_wake(job: Job) -> None:
    """Decide o destino de um job concluído: se ninguém esperou e o chat não tem geração
    ativa, acorda um turno novo. Espera a geração corrente (se houver) terminar antes."""
    from ..chat import generation

    async def _settle() -> None:
        job.dispatched = True
        # roda no main loop (reaper) → offloada o psycopg2 síncrono p/ não travar o loop
        # até o connect_timeout se o banco engasgar. Desfecho entregue → não é órfão.
        await run_in_threadpool(_db_update, job.id, settled=True)

    if job.dispatched or job.waited or not job.chat_id:
        await _settle()
        return
    # espera o chat ficar ocioso (o agente pode ter soltado o job e ainda estar redigindo
    # o texto de encerramento do turno atual). Teto curto p/ não pendurar o reaper.
    for _ in range(120):  # ~120 * 1s
        gen = generation.get_active(job.chat_id)
        if gen is None or gen.done:
            break
        if job.waited:      # um wait_job apareceu nesse meio tempo
            await _settle()
            return
        await asyncio.sleep(1.0)
    if job.waited:
        await _settle()
        return
    await _settle()
    try:
        await _fire_wake(job)
    except Exception:  # noqa: BLE001 - wake é best-effort, nunca derruba o reaper
        logger.exception("wake do job %s falhou", job.id)


async def _reaper() -> None:
    """Varre jobs concluídos e agenda o wake dos que ninguém esperou. Roda no loop
    principal (subido no lifespan), então pode chamar generation.start via _fire_wake."""
    while True:
        try:
            for job in list(_jobs.values()):
                if job._evt.is_set() and not job.dispatched and not job.waited:
                    asyncio.create_task(_maybe_wake(job))
            # expira jobs concluídos antigos (evita crescer sem fim)
            now = time.monotonic()
            for jid, job in list(_jobs.items()):
                if not job.running and job.dispatched and job.ended_at and now - job.ended_at > 3600:
                    _jobs.pop(jid, None)
        except Exception:  # noqa: BLE001
            logger.exception("reaper de exec_jobs falhou")
        await asyncio.sleep(2.0)


def start_reaper() -> None:
    """Sobe o reaper no loop atual (chamado no lifespan do app). Idempotente."""
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper())


async def recover_orphans() -> None:
    """RECUPERAÇÃO no boot: jobs com `settled=False` no banco que NÃO estão no `_jobs`
    deste processo são órfãos de um restart — o subprocesso morreu e o chat que esperava
    o wake ficou travado. Marca-os interrompidos e ACORDA o chat com um aviso (destrava
    o 'wake que nunca chega'). A guarda `not in _jobs` evita pegar um job novo que acabou
    de iniciar. Nunca levanta; roda em background p/ não atrasar o readiness."""
    try:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import delete, select

        from ..db import SessionLocal
        from ..models import ExecJob
        async with SessionLocal() as db:
            # poda registros já resolvidos e antigos (não crescer sem fim)
            await db.execute(delete(ExecJob).where(
                ExecJob.settled.is_(True),
                ExecJob.updated_at < datetime.now(timezone.utc) - timedelta(days=7)))
            rows = list(await db.scalars(select(ExecJob).where(ExecJob.settled.is_(False))))
            orphans = [j for j in rows if j.job_key not in _jobs]  # exclui jobs deste processo
            if not orphans:
                await db.commit()
                return
            for j in orphans:
                if j.status == "running":
                    j.status = "interrupted"
                j.settled = True
            await db.commit()
        logger.warning("recuperação de exec_jobs: %d job(s) órfão(s) de um restart", len(orphans))
    except Exception:  # noqa: BLE001
        logger.exception("recover_orphans (query/mark) falhou")
        return

    from .. import health_service
    woken = 0
    for j in orphans:
        # sync `record` de propósito: recover_orphans é one-shot de BOOT (o loop ainda
        # não serve tráfego) e é raro — o custo de bloqueio é irrelevante aqui, ao
        # contrário do hot-path por-turno (que usa record_bg).
        health_service.record(
            "exec_jobs", "interrupted", severity="warn",
            detail={"command": (j.command or "")[:200]},
            user_id=str(j.user_id) if j.user_id else None,
            chat_id=str(j.chat_id) if j.chat_id else None,
        )
        # acorda o chat p/ destravar (capado — evita estampido de N turnos no boot).
        if j.chat_id and woken < 15:
            woken += 1
            try:
                from ..chat import resume
                note = (
                    f"[Comando em background interrompido] `{j.command}` foi cortado por um "
                    "restart do servidor e não chegou a terminar. Se ainda precisar do "
                    "resultado, rode o comando de novo."
                )
                await resume.resume_chat_turn(
                    str(j.chat_id), note,
                    notify_title="Comando em background interrompido",
                    notify_body=(j.command or "")[:120],
                )
            except Exception:  # noqa: BLE001 - wake é best-effort
                logger.exception("wake de recuperação falhou (job %s)", j.job_key)


async def shutdown(timeout: float = 5.0) -> None:
    """Mata as árvores de processo dos jobs em andamento no shutdown do servidor."""
    running = [j for j in _jobs.values() if j.running and j.proc is not None]
    for job in running:
        try:
            exec_service.kill_tree(job.proc)
            job.status = "killed"
        except Exception:  # noqa: BLE001
            pass
    if _reaper_task is not None:
        _reaper_task.cancel()
