"""Worktrees do Codespace: tarefas isoladas (branch própria) por agente.

Cada tarefa é um `git worktree` irmão do `src` (`<proj>/wt/<task_id>`), numa branch
`codespace/<task_id>`. O agente escreve ali sem tocar o `src` nem outros agentes; o
humano revisa o diff e aprova (merge no branch do projeto) ou descarta. Reusa os
helpers git do graph_service (`_git`/`_git_commit`/`push`/`_reindex_after_write`).

Como as tools rodam SÍNCRONAS via `asyncio.run` (loop novo), cada função cria seu
próprio engine (NullPool) — mesmo motivo de `graph_service.load_project`.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..models import CodespaceProject, CodespaceTask
from . import graph_service

logger = logging.getLogger(__name__)

_ACTIVE = ("running", "awaiting_review", "error")


def _engine():
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def _agit(root: Path, *args: str, timeout: float = 60):
    """git -C root … num thread (não bloqueia o event loop)."""
    return await run_in_threadpool(graph_service._git, root, *args, timeout=timeout)


def _task_out(t: CodespaceTask) -> dict[str, Any]:
    return {
        "id": str(t.id), "title": t.title or "", "agent": t.agent or "",
        "branch": t.branch or "", "base_branch": t.base_branch or "",
        "status": t.status, "diff_stat": t.diff_stat or {},
        "test_status": t.test_status, "error": t.error_message,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _numstat(root: Path, base: str) -> dict[str, int]:
    proc = graph_service._git(root, "diff", "--numstat", base)
    files = ins = dele = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            files += 1
            if parts[0].isdigit():
                ins += int(parts[0])
            if parts[1].isdigit():
                dele += int(parts[1])
    return {"files": files, "insertions": ins, "deletions": dele}


async def _load_owned(db, user_id: str, task_id: str) -> CodespaceTask | None:
    try:
        tid = uuid.UUID(str(task_id))
    except ValueError:
        return None
    t = await db.get(CodespaceTask, tid)
    if t is None or str(t.user_id) != str(user_id):
        return None
    # registra o caminho da pasta (source='folder') p/ working_copy_path resolver certo
    proj = await db.get(CodespaceProject, t.project_id)
    if proj is not None:
        graph_service.register_folder(proj)
    return t


# --------------------------------------------------------------------------- #
# Abrir / listar / diff
# --------------------------------------------------------------------------- #
async def open_task(user_id: str, project_id: str, *, title: str = "", agent: str = "",
                    chat_id: str | None = None, base: str = "") -> dict[str, Any]:
    s = get_settings()
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                pid = uuid.UUID(str(project_id))
            except ValueError:
                return {"error": "projeto inválido"}
            proj = await db.get(CodespaceProject, pid)
            if proj is None or str(proj.user_id) != str(user_id):
                return {"error": "projeto não encontrado (ou não pertence a este usuário)"}
            graph_service.register_folder(proj)
            if proj.index_status != "ready":
                return {"error": "o projeto ainda não está pronto (clonando/indexando) — aguarde"}
            n = await db.scalar(
                select(func.count()).select_from(CodespaceTask)
                .where(CodespaceTask.project_id == proj.id, CodespaceTask.status.in_(("running", "awaiting_review")))
            )
            if int(n or 0) >= int(s.codespace_max_worktrees_per_user):
                return {"error": f"limite de {s.codespace_max_worktrees_per_user} tarefas abertas atingido — "
                                 "mescle ou descarte alguma antes de abrir outra"}
            base = (base or proj.branch or "main").strip()
            chat_uuid = None
            if chat_id:
                try:
                    chat_uuid = uuid.UUID(str(chat_id))
                except ValueError:
                    chat_uuid = None
            task = CodespaceTask(
                project_id=proj.id, user_id=proj.user_id, chat_id=chat_uuid,
                agent=(agent or "")[:120], title=(title or "")[:2000],
                base_branch=base, status="running",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            tid = str(task.id)
            src = graph_service.working_copy_path(user_id, project_id)
            wt = graph_service.wt_dir(user_id, project_id, tid)
            branch = f"codespace/{tid}"
            wt.parent.mkdir(parents=True, exist_ok=True)
            proc = await _agit(src, "worktree", "add", "-b", branch, str(wt), base, timeout=120)
            if proc.returncode != 0:
                task.status = "error"
                task.error_message = (proc.stderr or proc.stdout).strip()[-400:]
                await db.commit()
                return {"error": f"git worktree add falhou: {task.error_message}"}
            task.branch = branch
            task.worktree_path = str(wt)
            await db.commit()
            await db.refresh(task)
            return _task_out(task)
    finally:
        await eng.dispose()


async def list_tasks(user_id: str, project_id: str) -> dict[str, Any]:
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                pid = uuid.UUID(str(project_id))
            except ValueError:
                return {"tasks": []}
            rows = list(await db.scalars(
                select(CodespaceTask).where(
                    CodespaceTask.project_id == pid, CodespaceTask.user_id == uuid.UUID(str(user_id))
                ).order_by(CodespaceTask.created_at.desc())
            ))
            return {"tasks": [_task_out(t) for t in rows]}
    finally:
        await eng.dispose()


async def task_diff(user_id: str, task_id: str) -> dict[str, Any]:
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            t = await _load_owned(db, user_id, task_id)
            if t is None:
                return {"error": "tarefa não encontrada"}
            wt = Path(t.worktree_path) if t.worktree_path else None
            if not wt or not wt.exists():
                return {"error": "worktree não existe mais (tarefa mesclada/descartada)", "status": t.status}
            base = t.base_branch or "main"
            proc = await _agit(wt, "diff", base)
            diff = proc.stdout if proc.returncode == 0 else (proc.stderr or "")
            stat = await run_in_threadpool(_numstat, wt, base)
            t.diff_stat = stat
            await db.commit()
            if len(diff) > graph_service._MAX_FILE_BYTES:
                diff = diff[: graph_service._MAX_FILE_BYTES] + "\n…[truncado]"
            return {"diff": diff or "(sem mudanças)", "diff_stat": stat, "status": t.status}
    finally:
        await eng.dispose()


def _names(root: Path, base: str) -> list[str]:
    """Arquivos tocados por uma tarefa (vs a base) — barato (--name-only)."""
    proc = graph_service._git(root, "diff", "--name-only", base)
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


async def synthesize_merge(user_id: str, project_id: str) -> dict[str, Any]:
    """Síntese assistida de merge: reúne as tarefas ABERTAS (running/awaiting_review)
    do projeto, os arquivos que cada uma toca e as SOBREPOSIÇÕES entre elas (arquivo
    mexido por >1 tarefa = risco de conflito). Não mescla nada — devolve o material
    para o orquestrador propor a ordem de integração e resolver conflitos antes de
    chamar `merge` tarefa a tarefa. Cada worktree é independente, então o git só vê o
    conflito no momento do merge; esta visão antecipa isso."""
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                pid = uuid.UUID(str(project_id))
            except ValueError:
                return {"error": "projeto inválido"}
            rows = list(await db.scalars(
                select(CodespaceTask).where(
                    CodespaceTask.project_id == pid,
                    CodespaceTask.user_id == uuid.UUID(str(user_id)),
                    CodespaceTask.status.in_(("running", "awaiting_review")),
                ).order_by(CodespaceTask.created_at)
            ))
            tasks: list[dict[str, Any]] = []
            by_file: dict[str, list[str]] = {}
            for t in rows:
                wt = Path(t.worktree_path) if t.worktree_path else None
                files = (
                    await run_in_threadpool(_names, wt, t.base_branch or "main")
                    if wt and wt.exists() else []
                )
                for f in files:
                    by_file.setdefault(f, []).append(str(t.id))
                tasks.append({
                    "id": str(t.id), "title": t.title or "", "agent": t.agent or "",
                    "status": t.status, "test_status": t.test_status,
                    "diff_stat": t.diff_stat or {}, "files": files[:60],
                })
            overlaps = [
                {"file": f, "tasks": ids}
                for f, ids in sorted(by_file.items()) if len(ids) > 1
            ]
            note = (
                "Sem tarefas abertas para integrar." if not tasks else
                ("Nenhuma sobreposição de arquivos — as tarefas podem ser mescladas em qualquer ordem."
                 if not overlaps else
                 "Há arquivos tocados por mais de uma tarefa (abaixo, em `overlaps`): mescle uma, "
                 "revise/rebase as outras e resolva o conflito ANTES de mesclá-las.")
            )
            return {"tasks": tasks, "overlaps": overlaps, "n_awaiting": len(tasks), "note": note}
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Mesclar / PR / descartar
# --------------------------------------------------------------------------- #
async def merge_task(user_id: str, task_id: str, *, push: bool = False) -> dict[str, Any]:
    """Mescla a branch do worktree no branch do projeto (merge local, --no-ff).
    Conflito → aborta e marca `error` (branch do projeto intacta). Sucesso →
    reindexa o grafo, remove o worktree/branch e (se `push`) envia ao remoto."""
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            t = await _load_owned(db, user_id, task_id)
            if t is None:
                return {"error": "tarefa não encontrada"}
            if t.status == "merged":
                return {"ok": True, "already": True}
            wt = Path(t.worktree_path) if t.worktree_path else None
            if not wt or not wt.exists():
                return {"error": "worktree não existe mais"}
            src = graph_service.working_copy_path(user_id, str(t.project_id))
            branch = t.branch
            # 1) commita qualquer resto não-commitado no worktree
            await run_in_threadpool(graph_service._git_commit, wt, "AI: snapshot antes do merge")
            # 2) merge no src (que está no branch do projeto). O --no-ff cria um commit
            #    de merge → precisa de identidade git (senão "Committer identity unknown",
            #    rc 128), passada por -c como no _git_commit.
            name, email = graph_service._GIT_AUTHOR
            proc = await _agit(src, "-c", f"user.name={name}", "-c", f"user.email={email}",
                               "merge", "--no-ff", branch, "-m",
                               f"Merge tarefa: {(t.title or branch)[:80]}", timeout=120)
            if proc.returncode != 0:
                await _agit(src, "merge", "--abort")
                t.status = "error"
                t.error_message = "conflito de merge: " + (proc.stdout or proc.stderr).strip()[-300:]
                await db.commit()
                return {"error": "conflito de merge — resolva no worktree e tente de novo, ou descarte a tarefa",
                        "conflict": True}
            # 3) reindexa o grafo do projeto (agora o src mudou)
            await run_in_threadpool(graph_service._reindex_after_write, user_id, str(t.project_id))
            # 4) remove worktree + branch
            await _agit(src, "worktree", "remove", "--force", str(wt))
            await _agit(src, "branch", "-D", branch)
            t.status = "merged"
            t.worktree_path = ""
            await db.commit()
            out: dict[str, Any] = {"ok": True, "merged": branch}
            if push:
                pr = await graph_service.push(user_id, str(t.project_id))
                out["push"] = pr
            return out
    finally:
        await eng.dispose()


async def open_pr(user_id: str, task_id: str, *, title: str = "", body: str = "") -> dict[str, Any]:
    """Empurra a branch do worktree para o remoto e abre um Pull Request
    (branch → branch do projeto). Só para projetos com conta GitHub vinculada."""
    from ..integrations import github_service

    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            t = await _load_owned(db, user_id, task_id)
            if t is None:
                return {"error": "tarefa não encontrada"}
            proj = await db.get(CodespaceProject, t.project_id)
            if proj is None:
                return {"error": "projeto não encontrado"}
            if proj.source not in ("git", "git-ssh") or not proj.github_account_id:
                return {"error": "abrir PR requer um projeto com conta GitHub vinculada"}
            repo = _repo_slug(proj.repo_url)
            if not repo:
                return {"error": "não consegui identificar owner/repo a partir da URL do projeto"}
            wt = Path(t.worktree_path) if t.worktree_path else None
            if not wt or not wt.exists():
                return {"error": "worktree não existe mais"}
            await run_in_threadpool(graph_service._git_commit, wt, "AI: snapshot antes do PR")
            token = await github_service.get_token(str(proj.github_account_id))
            if not token:
                return {"error": "não consegui obter o token da conta GitHub"}
            # push da branch do worktree ao remoto (auth por header, não na URL)
            try:
                await run_in_threadpool(graph_service._push, wt, t.branch, token, None)
            except Exception as exc:  # noqa: BLE001
                return {"error": f"push da branch falhou: {str(exc)[:300]}"}
            try:
                pr = await run_in_threadpool(
                    github_service.create_pr, token, repo,
                    (title or t.title or t.branch)[:120], t.branch, proj.branch,
                    body or "PR aberto pelo Codespace (AI Workspace).",
                )
            except Exception as exc:  # noqa: BLE001
                return {"error": f"não consegui abrir o PR: {str(exc)[:300]}"}
            t.status = "awaiting_review"
            await db.commit()
            return {"ok": True, "pr": pr}
    finally:
        await eng.dispose()


async def discard_task(user_id: str, task_id: str) -> dict[str, Any]:
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            t = await _load_owned(db, user_id, task_id)
            if t is None:
                return {"error": "tarefa não encontrada"}
            src = graph_service.working_copy_path(user_id, str(t.project_id))
            wt = Path(t.worktree_path) if t.worktree_path else None
            if wt and wt.exists():
                await _agit(src, "worktree", "remove", "--force", str(wt))
            if t.branch:
                await _agit(src, "branch", "-D", t.branch)
            t.status = "discarded"
            t.worktree_path = ""
            await db.commit()
            return {"ok": True, "discarded": t.branch}
    finally:
        await eng.dispose()


async def set_test_status(task_id: str, status: str) -> None:
    """Grava o resultado do test_command (pass/fail) na tarefa (loop de verificação)."""
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                t = await db.get(CodespaceTask, uuid.UUID(str(task_id)))
            except ValueError:
                return
            if t is not None:
                t.test_status = status
                await db.commit()
    finally:
        await eng.dispose()


async def mark_awaiting(task_id: str) -> None:
    """Marca a tarefa como pronta p/ revisão (o worker terminou). Recalcula o diff_stat."""
    eng = _engine()
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                t = await db.get(CodespaceTask, uuid.UUID(str(task_id)))
            except ValueError:
                return
            if t is None or t.status not in ("running",):
                return
            wt = Path(t.worktree_path) if t.worktree_path else None
            if wt and wt.exists():
                await run_in_threadpool(graph_service._git_commit, wt, "AI: fim da tarefa")
                t.diff_stat = await run_in_threadpool(_numstat, wt, t.base_branch or "main")
            t.status = "awaiting_review"
            await db.commit()
    finally:
        await eng.dispose()


def _repo_slug(repo_url: str) -> str:
    """https://github.com/owner/name(.git) | git@github.com:owner/name.git → owner/name."""
    u = (repo_url or "").strip()
    if not u:
        return ""
    u = u.split("://", 1)[-1]
    if "@" in u and ":" in u and "/" not in u.split(":", 1)[0]:
        u = u.split(":", 1)[1]  # forma SSH
    else:
        parts = u.split("/", 1)
        u = parts[1] if len(parts) == 2 else u
    if u.endswith(".git"):
        u = u[:-4]
    segs = [p for p in u.split("/") if p]
    return "/".join(segs[:2]) if len(segs) >= 2 else ""


# --------------------------------------------------------------------------- #
# Reaper (ciclo de vida): descarta worktrees ociosos além do TTL
# --------------------------------------------------------------------------- #
async def reap_stale() -> int:
    s = get_settings()
    ttl = int(s.codespace_worktree_ttl_seconds)
    if ttl <= 0:
        return 0
    cutoff = time.time() - ttl
    eng = _engine()
    n = 0
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            rows = list(await db.scalars(
                select(CodespaceTask).where(CodespaceTask.status.in_(("running", "awaiting_review", "error")))
            ))
            stale = [t for t in rows if t.updated_at and t.updated_at.timestamp() < cutoff]
        for t in stale:
            try:
                await discard_task(str(t.user_id), str(t.id))
                n += 1
            except Exception:  # noqa: BLE001
                logger.warning("reaper: falha ao descartar worktree %s", t.id, exc_info=True)
    finally:
        await eng.dispose()
    return n
