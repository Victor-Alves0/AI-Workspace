"""Codespace: CRUD de projetos (repositório clonado + grafo de código).

Espelha `knowledge_routes.py` (indexação em background, status pending→
cloning→indexing→ready/error). Slice 1: só leitura, origem `git` (clone HTTPS).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .codespace import graph_service, preview_service, worktree_service
from .db import get_db
from .memory import mem0_service
from .models import Chat, CodespaceProject, GithubAccount, MemoryBank, User
from .secrets_service import OPENROUTER_KEY, get_secret

router = APIRouter(prefix="/codespace", tags=["codespace"])

_spawn_index = graph_service.spawn_index
_spawn_refine = graph_service.spawn_refine


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ProjectIn(BaseModel):
    name: str
    # "git" | "git-ssh" | "local" | "folder" (abre um diretório existente no host)
    source: str = "git"
    repo_url: str = ""
    branch: str = "main"
    github_account_id: str | None = None
    # source="folder": caminho absoluto de um diretório existente no servidor
    local_path: str = ""


class ProjectScopeIn(BaseModel):
    # {"allow": ["src/**"], "deny": ["tests/**"]} — globs estilo .gitignore
    allow: list[str] | None = None
    deny: list[str] | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    scope: ProjectScopeIn | None = None
    # modelo padrão dos novos chats do projeto ("custom:<id>" ou modelo base);
    # "" limpa (volta a herdar o padrão do usuário)
    default_model: str | None = None
    # sandbox de execução (tool code.exec.run)
    setup_command: str | None = None
    test_command: str | None = None
    exec_enabled: bool | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    source: str
    repo_url: str
    branch: str
    github_account_id: str | None = None
    # deploy key pública (source="git-ssh") — cole no GitHub/GitLab/VPS. A
    # PRIVADA nunca sai do servidor (nem este schema a expõe).
    ssh_public_key: str | None = None
    # banco de memória do projeto (criado junto, compartilhado pelos chats vinculados)
    memory_bank_id: str | None = None
    # modelo padrão dos novos chats do projeto (nulo = padrão do usuário)
    default_model: str | None = None
    scope: dict = {}
    index_status: str
    error_message: str | None = None
    stats: dict | None = None
    last_indexed_at: str | None = None
    # sandbox de execução
    setup_command: str = ""
    test_command: str = ""
    exec_enabled: bool = False
    local_path: str | None = None


def _out(p: CodespaceProject) -> ProjectOut:
    return ProjectOut(
        id=str(p.id), name=p.name, source=p.source, repo_url=p.repo_url, branch=p.branch,
        github_account_id=str(p.github_account_id) if p.github_account_id else None,
        ssh_public_key=p.ssh_public_key,
        memory_bank_id=str(p.memory_bank_id) if p.memory_bank_id else None,
        default_model=p.default_model,
        scope=p.scope or {}, index_status=p.index_status, error_message=p.error_message,
        stats=p.stats, last_indexed_at=p.last_indexed_at.isoformat() if p.last_indexed_at else None,
        setup_command=p.setup_command or "", test_command=p.test_command or "",
        exec_enabled=bool(p.exec_enabled), local_path=p.local_path,
    )


async def _owned_project(db: AsyncSession, user: User, project_id: uuid.UUID) -> CodespaceProject:
    p = await db.get(CodespaceProject, project_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projeto não encontrado")
    return p


async def _ensure_memory_bank(db: AsyncSession, user: User, p: CodespaceProject) -> None:
    """Cria o banco de memória do projeto se ainda não existir (projetos criados
    antes deste recurso, ou criação que falhou no meio)."""
    if p.memory_bank_id:
        return
    bank = MemoryBank(user_id=user.id, name=p.name[:120])
    db.add(bank)
    await db.commit()
    await db.refresh(bank)
    p.memory_bank_id = bank.id
    await db.commit()
    await db.refresh(p)


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(
        select(CodespaceProject).where(CodespaceProject.user_id == user.id)
        .order_by(CodespaceProject.created_at)
    ))
    return [_out(p) for p in rows]


_SOURCES = ("git", "git-ssh", "local", "folder")


@router.post("/projects", response_model=ProjectOut)
async def create_project(
    body: ProjectIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    source = (body.source or "git").strip().lower()
    if source not in _SOURCES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"origem inválida (use: {', '.join(_SOURCES)})")
    repo_url = (body.repo_url or "").strip()
    if source in ("git", "git-ssh") and not repo_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "repo_url é obrigatório")
    local_path = (body.local_path or "").strip()
    if source == "folder":
        import os
        if not local_path or not os.path.isdir(local_path):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "informe local_path — um diretório existente no servidor")

    gh_id: uuid.UUID | None = None
    if source == "git" and body.github_account_id:
        try:
            gh_id = uuid.UUID(body.github_account_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "github_account_id inválido")
        acc = await db.get(GithubAccount, gh_id)
        if acc is None or acc.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conta GitHub não encontrada")

    ssh_priv: str | None = None
    ssh_pub: str | None = None
    if source == "git-ssh":
        try:
            ssh_priv, ssh_pub = await run_in_threadpool(graph_service.generate_ssh_keypair)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"falha ao gerar a deploy key: {exc}")

    p = CodespaceProject(
        user_id=user.id, name=(body.name or "Projeto").strip()[:255],
        source=source, repo_url=repo_url, branch=(body.branch or "main").strip()[:120] or "main",
        github_account_id=gh_id, ssh_private_key=ssh_priv, ssh_public_key=ssh_pub,
        local_path=local_path or None, scope={}, index_status="pending",
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)

    # banco de memória do projeto: os chats vinculados a ele leem/escrevem aqui
    # por padrão (ver chat/routes.create_chat), compartilhando contexto entre si.
    await _ensure_memory_bank(db, user, p)

    _spawn_index(p.id)
    return _out(p)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    p = await _owned_project(db, user, project_id)
    return _out(p)


@router.get("/projects/{project_id}/commits")
async def list_commits(
    project_id: uuid.UUID, limit: int = 20,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Histórico de commits da working copy — inclui os que a IA fez (todo
    write/edit/delete commita automaticamente, ver [[graph_service]])."""
    p = await _owned_project(db, user, project_id)
    return await run_in_threadpool(graph_service.git_log, str(user.id), str(p.id), limit)


class ChatLite(BaseModel):
    id: str
    title: str
    updated_at: str
    archived: bool


@router.get("/projects/{project_id}/chats", response_model=list[ChatLite])
async def list_project_chats(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Chats já vinculados a este projeto — pra "Abrir chat" oferecer continuar
    uma conversa existente em vez de sempre criar uma nova."""
    p = await _owned_project(db, user, project_id)
    rows = list(await db.scalars(
        select(Chat).where(Chat.project_id == p.id, Chat.user_id == user.id, Chat.archived.is_(False))
        .order_by(Chat.updated_at.desc())
    ))
    return [ChatLite(id=str(c.id), title=c.title, updated_at=c.updated_at.isoformat(), archived=c.archived) for c in rows]


@router.get("/projects/{project_id}/files")
async def browse_files(
    project_id: uuid.UUID, path: str = "", depth: int = 2,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Árvore de arquivos (mesma função que a tool code.files.browse usa) — pro
    explorador do Espaço de Trabalho, não só pra IA."""
    p = await _owned_project(db, user, project_id)
    try:
        return await run_in_threadpool(
            graph_service.list_files, str(p.user_id), str(p.id), p.scope or {}, path, max(1, min(depth, 5))
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/projects/{project_id}/files/content")
async def read_file_route(
    project_id: uuid.UUID, path: str, start_line: int = 1, end_line: int | None = None,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    try:
        return await run_in_threadpool(
            graph_service.read_file, str(p.user_id), str(p.id), p.scope or {}, path, start_line, end_line
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


class FileWriteIn(BaseModel):
    path: str
    content: str
    message: str = ""


@router.put("/projects/{project_id}/files/content")
async def write_file_route(
    project_id: uuid.UUID, body: FileWriteIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Edição HUMANA de um arquivo pelo editor do Espaço de Trabalho — reusa a
    MESMA `write_file` que a tool da IA usa (auto-commit local); diferente da
    tool, não passa pelo toggle "confirmar ações" (é o próprio usuário confirmando
    ao clicar Salvar, não uma ação autônoma de agente)."""
    p = await _owned_project(db, user, project_id)
    if p.index_status not in ("indexing", "ready"):
        raise HTTPException(status.HTTP_409_CONFLICT, "projeto ainda não está pronto")
    if not body.path.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path é obrigatório")
    try:
        result = await run_in_threadpool(
            graph_service.write_file, str(p.user_id), str(p.id), p.scope or {},
            body.path.strip(), body.content, body.message,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if result.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result["error"])
    return result


class FileMoveIn(BaseModel):
    path: str
    dest_path: str
    message: str = ""


@router.patch("/projects/{project_id}/files/move")
async def move_file_route(
    project_id: uuid.UUID, body: FileMoveIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Mover/renomear (arraste-e-solte ou "Renomear" no explorador) — mesmo
    commit automático das outras escritas humanas, sem o gate de confirmação."""
    p = await _owned_project(db, user, project_id)
    if p.index_status not in ("indexing", "ready"):
        raise HTTPException(status.HTTP_409_CONFLICT, "projeto ainda não está pronto")
    if not body.path.strip() or not body.dest_path.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "path e dest_path são obrigatórios")
    try:
        result = await run_in_threadpool(
            graph_service.move_file, str(p.user_id), str(p.id), p.scope or {},
            body.path.strip(), body.dest_path.strip(), body.message,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if result.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result["error"])
    return result


@router.get("/projects/{project_id}/files/search")
async def search_files_by_name_route(
    project_id: uuid.UUID, q: str = "", limit: int = 100,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Busca por NOME de arquivo (o explorador do chat/Espaço de Trabalho) —
    diferente de `code.files.search`/tool, que busca por CONTEÚDO."""
    p = await _owned_project(db, user, project_id)
    try:
        return await run_in_threadpool(
            graph_service.find_files, str(p.user_id), str(p.id), p.scope or {}, q, limit
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/projects/{project_id}/graph/visualize")
async def graph_visualize(
    project_id: uuid.UUID, level: str = "file", scope: str = "", top: int = 200,
    mode: str = "", symbol: str = "", depth: int = 3,
    min_confidence: str = "", language: str = "",
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Grafo do projeto pra visão estilo Obsidian. `level` (file|symbol) OU um `mode`
    semeado por `symbol` (neighborhood|callers|callees|impact|domains), com filtros
    `min_confidence`/`language` — diferente de `graph/ego` (só a vizinhança direta)."""
    p = await _owned_project(db, user, project_id)
    if p.index_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "projeto ainda não está pronto (aguarde a indexação)")
    return await run_in_threadpool(
        graph_service.visualize, str(p.user_id), str(p.id), level, scope, top,
        mode, symbol, depth, min_confidence, language,
    )


# --------------------------------------------------------------------------- #
# Preview vivo (dev servers no ar) — a IA sobe via a tool; a UI só lista/olha/para
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/previews")
async def list_previews(
    project_id: uuid.UUID,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    return await run_in_threadpool(preview_service.list_previews, str(p.user_id), str(p.id))


@router.get("/projects/{project_id}/previews/{preview_id}")
async def preview_status(
    project_id: uuid.UUID, preview_id: str,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    return await run_in_threadpool(preview_service.preview_status, str(p.user_id), preview_id)


@router.post("/projects/{project_id}/previews/{preview_id}/stop")
async def stop_preview(
    project_id: uuid.UUID, preview_id: str,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    return await run_in_threadpool(preview_service.stop_preview, str(p.user_id), preview_id)


@router.get("/projects/{project_id}/graph/find")
async def graph_find(
    project_id: uuid.UUID, query: str = "", limit: int = 15,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    if p.index_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "projeto ainda não está pronto (aguarde a indexação)")
    if not query.strip():
        return {"symbols": [], "warnings": []}
    return await run_in_threadpool(graph_service.find, str(p.user_id), str(p.id), query.strip(), limit)


@router.get("/projects/{project_id}/graph/ego")
async def graph_ego(
    project_id: uuid.UUID, symbol: str,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Vizinhança de um símbolo (pai/filhos/quem ele chama/quem o chama) — mesma
    query da tool code.graph.query (action=ego), pra visualização no Espaço de
    Trabalho (aba Grafo)."""
    p = await _owned_project(db, user, project_id)
    if p.index_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "projeto ainda não está pronto (aguarde a indexação)")
    return await run_in_threadpool(graph_service.ego, str(p.user_id), str(p.id), symbol)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    if body.name is not None:
        p.name = body.name.strip()[:255] or p.name
        if p.memory_bank_id:
            bank = await db.get(MemoryBank, p.memory_bank_id)
            if bank is not None:
                bank.name = p.name[:120]
    if body.default_model is not None:
        p.default_model = body.default_model.strip()[:255] or None
    if body.scope is not None:
        p.scope = {"allow": body.scope.allow or [], "deny": body.scope.deny or []}
    if body.setup_command is not None:
        p.setup_command = body.setup_command.strip()[:2000]
    if body.test_command is not None:
        p.test_command = body.test_command.strip()[:2000]
    if body.exec_enabled is not None:
        p.exec_enabled = bool(body.exec_enabled)
    await db.commit()
    await db.refresh(p)
    return _out(p)


@router.post("/projects/{project_id}/reindex", response_model=ProjectOut)
async def reindex_project(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Reindexa em cima da working copy ATUAL — NÃO reclona, não descarta nada
    (inclusive commits que a IA já tenha feito). Seguro de chamar a qualquer
    momento; use 'Ressincronizar' se quiser puxar do zero a partir da origem."""
    p = await _owned_project(db, user, project_id)
    if p.index_status in ("cloning", "indexing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Já está indexando")
    p.index_status = "pending"
    p.error_message = None
    await db.commit()
    await db.refresh(p)
    _spawn_index(p.id, reclone=False)
    return _out(p)


@router.post("/projects/{project_id}/resync", response_model=ProjectOut)
async def resync_project(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Reclona do ZERO a partir da origem remota — DESCARTA qualquer commit
    local não enviado (inclusive os que a IA fez). Só use se quiser jogar fora
    o trabalho local e puxar de novo o que está no GitHub."""
    p = await _owned_project(db, user, project_id)
    if p.source in ("local", "folder"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Este projeto não tem origem remota para ressincronizar")
    if p.index_status in ("cloning", "indexing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Já está indexando")
    p.index_status = "pending"
    p.error_message = None
    await db.commit()
    await db.refresh(p)
    _spawn_index(p.id, reclone=True)
    return _out(p)


@router.post("/projects/{project_id}/memory-bank", response_model=ProjectOut)
async def create_memory_bank(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Cria o banco de memória de projetos criados antes deste recurso existir."""
    p = await _owned_project(db, user, project_id)
    await _ensure_memory_bank(db, user, p)
    return _out(p)


@router.post("/projects/{project_id}/refine")
async def refine_project(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Resolução L1 (jedi): promove arestas 'inferred'/'possible' a 'certain'.
    Rodada à parte da indexação normal — é lenta mesmo em repos pequenos."""
    p = await _owned_project(db, user, project_id)
    if p.index_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "Projeto precisa estar indexado (ready) primeiro")
    _spawn_refine(p.id, str(p.user_id))
    return {"ok": True, "note": "refinamento iniciado em background (pode levar alguns minutos)"}


# --------------------------------------------------------------------------- #
# Tarefas (worktrees isolados) — revisão/merge do trabalho dos agentes
# --------------------------------------------------------------------------- #
class TaskOpenIn(BaseModel):
    title: str = ""
    agent: str = ""
    base: str = ""


class TaskMergeIn(BaseModel):
    push: bool = False
    open_pr: bool = False
    title: str = ""
    body: str = ""


@router.get("/projects/{project_id}/tasks")
async def list_tasks_route(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    p = await _owned_project(db, user, project_id)
    return await worktree_service.list_tasks(str(user.id), str(p.id))


@router.post("/projects/{project_id}/tasks")
async def open_task_route(
    project_id: uuid.UUID, body: TaskOpenIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    p = await _owned_project(db, user, project_id)
    if p.index_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "projeto ainda não está pronto (aguarde a indexação)")
    res = await worktree_service.open_task(str(user.id), str(p.id), title=body.title, agent=body.agent, base=body.base)
    if res.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, res["error"])
    return res


@router.get("/projects/{project_id}/tasks/{task_id}/diff")
async def task_diff_route(
    project_id: uuid.UUID, task_id: uuid.UUID,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _owned_project(db, user, project_id)
    return await worktree_service.task_diff(str(user.id), str(task_id))


@router.post("/projects/{project_id}/tasks/{task_id}/merge")
async def merge_task_route(
    project_id: uuid.UUID, task_id: uuid.UUID, body: TaskMergeIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _owned_project(db, user, project_id)
    if body.open_pr:
        res = await worktree_service.open_pr(str(user.id), str(task_id), title=body.title, body=body.body)
    else:
        res = await worktree_service.merge_task(str(user.id), str(task_id), push=body.push)
    if res.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, res["error"])
    return res


@router.post("/projects/{project_id}/tasks/{task_id}/discard")
async def discard_task_route(
    project_id: uuid.UUID, task_id: uuid.UUID,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _owned_project(db, user, project_id)
    res = await worktree_service.discard_task(str(user.id), str(task_id))
    if res.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, res["error"])
    return res


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    p = await _owned_project(db, user, project_id)
    if p.memory_bank_id:
        bank_id = p.memory_bank_id
        key = await get_secret(db, user.id, OPENROUTER_KEY) or "x"
        await run_in_threadpool(
            lambda: mem0_service.delete_scope(key, str(user.id), scope="bank", agent_id=f"bank:{bank_id}")
        )
        bank = await db.get(MemoryBank, bank_id)
        if bank is not None:
            await db.delete(bank)
    graph_service.delete_project_files(str(user.id), str(p.id))
    await db.delete(p)
    await db.commit()
    return {"ok": True}
