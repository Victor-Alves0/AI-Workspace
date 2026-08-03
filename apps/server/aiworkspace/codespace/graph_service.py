"""Serviço do Codespace: clona/indexa projetos e responde às tools de grafo de
código e arquivos.

**O código é a fonte da verdade; `.codegraph/graph.db` é um cache derivado**
(GraphCodeMap — projeto próprio do Victor: symbols, call graph, impact analysis,
local-first, staleness-aware). Guardamos a working copy + o índice fora do
repositório em si: `/data/codespace/<user_id>/<project_id>/{src,graph.db}`.

Clonagem e indexação rodam em BACKGROUND (mesmo padrão do `knowledge/ingest.py`:
engine efêmero NullPool + status pending→cloning→indexing→ready/error), então a
criação do projeto responde na hora. As queries (SQLite síncrono) e as operações
de arquivo (path-jail + escopo allow/deny) são baratas o bastante para rodar
inline no threadpool das tools SIFT.

Slice 1: só leitura, origem `git` (clone HTTPS, com o PAT/OAuth de uma conta
GitHub conectada quando o repo é privado). SSH fica para depois.

Slice 2: escrita de arquivos (write/edit/delete) + git. **Toda escrita vira um
commit LOCAL automático** — reversível, sem fricção, é o "sempre versionar as
etapas" que o Victor pediu. `push` (afeta o remoto — visível a terceiros, menos
reversível) é uma ação SEPARADA e explícita, sujeita ao mesmo toggle global
`confirm_actions` que já protege escritas do GitHub/Google/Tuya (padrão OFF).
`delete` também passa por esse toggle (destrutivo, mesmo commitado antes)."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pathspec
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..models import CodespaceProject, User

logger = logging.getLogger(__name__)

# Onde os projetos/worktrees vivem no disco. Configurável (get_settings) para
# funcionar fora do Linux/Docker (Windows/desktop) — ver Settings.codespace_data_dir.
_DATA_ROOT = Path(get_settings().codespace_data_dir)

# tetos anti-explosão de contexto: o ganho do grafo é reduzir o que volta ao
# modelo, não devolver o banco inteiro numa chamada.
_MAX_SIG = 800
_MAX_DOC = 300
_MAX_RESULTS = 30
_MAX_FILE_LINES = 400
_MAX_FILE_BYTES = 300_000
_MAX_LIST_ENTRIES = 300
_MAX_SEARCH_RESULTS = 40
_CLONE_TIMEOUT_S = 300

# instâncias CodeGraph vivas (uma por projeto) — reabrir a cada chamada reabriria
# o sqlite à toa; index()/reindex/clone invalida explicitamente.
_cache: dict[str, Any] = {}

# tasks de indexação em voo (evita GC prematuro do asyncio.create_task)
_TASKS: set = set()


# --------------------------------------------------------------------------- #
# Caminhos
# --------------------------------------------------------------------------- #
def _project_dir(user_id: str, project_id: str) -> Path:
    return _DATA_ROOT / str(user_id) / str(project_id)


# source="folder": a working copy é um diretório EXISTENTE no host (fora do
# codespace_data), não `<proj>/src`. Cache em-processo populado por `register_folder`
# em todo ponto que carrega o projeto — o graph.db e os worktrees continuam sob
# `<proj>/` (dentro do codespace_data), então apagar o projeto NUNCA toca a pasta.
_FOLDER_SRC: dict[str, str] = {}


def register_folder(project) -> None:
    """Registra (ou remove) o caminho da pasta de um projeto source='folder'."""
    try:
        pid = str(project.id)
        if getattr(project, "source", "") == "folder" and getattr(project, "local_path", ""):
            _FOLDER_SRC[pid] = project.local_path
    except Exception:  # noqa: BLE001
        pass


def working_copy_path(user_id: str, project_id: str) -> Path:
    ov = _FOLDER_SRC.get(str(project_id))
    return Path(ov) if ov else _project_dir(user_id, project_id) / "src"


def data_root() -> Path:
    """Raiz onde todos os projetos/worktrees vivem (para caminhos relativos do runner)."""
    return _DATA_ROOT


def project_data_dir(user_id: str, project_id: str) -> Path:
    """Dir de dados PERSISTENTE por projeto (`<proj>/data`), IRMÃO de `src`/`wt` — FORA do
    working copy git (não polui commits) e no volume codespace_data (sobrevive a restart/
    rebuild do server). Serviços apontam estado durável aqui (DB embarcado, uploads, caches)
    via a env WORKSPACE_DATA — é o que dá CONTINUIDADE de ambiente entre turnos. Vale também
    p/ source='folder' (o `<proj>/` existe no codespace_data mesmo com o `src` externo)."""
    d = _project_dir(user_id, project_id) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def wt_dir(user_id: str, project_id: str, task_id: str) -> Path:
    """Diretório do worktree de uma tarefa: `<proj>/wt/<task_id>` (irmão de `src`)."""
    return _project_dir(user_id, project_id) / "wt" / str(task_id)


def _db_path(user_id: str, project_id: str) -> Path:
    return _project_dir(user_id, project_id) / "graph.db"


# --------------------------------------------------------------------------- #
# CodeGraph — instância cacheada por projeto
# --------------------------------------------------------------------------- #
def _get_graph(user_id: str, project_id: str):
    from codegraph import CodeGraph  # import preguiçoso (como playwright no browser_driver)

    key = str(project_id)
    cg = _cache.get(key)
    if cg is None:
        root = working_copy_path(user_id, project_id)
        db_path = _db_path(user_id, project_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        cg = CodeGraph(str(root), db_path=str(db_path))
        _cache[key] = cg
    return cg


def invalidate(project_id: str) -> None:
    cg = _cache.pop(str(project_id), None)
    if cg is not None:
        try:
            cg.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Clonagem (git via HTTPS ou SSH) + projeto local (sem remoto)
# --------------------------------------------------------------------------- #
def _auth_header(token: str) -> str:
    # Basic auth por HEADER (não na URL): não fica gravado em .git/config da
    # working copy, então nenhuma tool de arquivo/skim consegue ler o token de volta.
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"Authorization: Basic {b64}"


def generate_ssh_keypair() -> tuple[str, str]:
    """Gera um par de chaves ed25519 (deploy key) NOVO por projeto — a privada
    nunca é vista pelo usuário (fica criptografada no banco); só a pública é
    exibida, pra ele colar como deploy key no GitHub/GitLab/VPS. Nunca reusa uma
    chave pessoal do usuário (diferente de "colar sua própria chave")."""
    with tempfile.TemporaryDirectory() as tmp:
        key_path = Path(tmp) / "id_ed25519"
        proc = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "codespace-ai", "-f", str(key_path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ssh-keygen falhou: {(proc.stderr or proc.stdout).strip()[-400:]}")
        return key_path.read_text(), (key_path.with_suffix(".pub")).read_text().strip()


def _ssh_env(private_key: str) -> tuple[dict[str, str], Any]:
    """Escreve a chave privada num arquivo temporário (0600) e devolve o
    `GIT_SSH_COMMAND` que aponta pra ele — o arquivo (via `tempfile.NamedTemporaryFile`,
    devolvido junto) precisa ficar vivo até o subprocess terminar; o chamador o
    fecha (e apaga) num `finally`. `UserKnownHostsFile=/dev/null` + `accept-new`:
    nunca persistimos known_hosts por-projeto, cada chamada reconecta do zero —
    aceitável (não é um loop de muitas chamadas)."""
    tf = tempfile.NamedTemporaryFile(mode="w", suffix="_id_ed25519", delete=False)
    tf.write(private_key if private_key.endswith("\n") else private_key + "\n")
    tf.close()
    os.chmod(tf.name, 0o600)
    ssh_cmd = (
        f"ssh -i {tf.name} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
        "-o UserKnownHostsFile=/dev/null -o BatchMode=yes"
    )
    return {"GIT_SSH_COMMAND": ssh_cmd}, tf


def _clone_or_refresh(repo_url: str, branch: str, dest: Path, token: str | None,
                       ssh_key: str | None = None) -> None:
    """(Re)clona um repo git (shallow, --depth 1) numa working copy nova.

    v1: sempre recria do zero (rm -rf + clone) em vez de fetch/pull incremental —
    mais simples e sem casos de borda de merge/estado sujo. O índice do
    GraphCodeMap é que faz a parte incremental de verdade (content-hash)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    cmd = ["git"]
    env = None
    keyfile = None
    if ssh_key:
        env_extra, keyfile = _ssh_env(ssh_key)
        env = {**os.environ, **env_extra}
    elif token:
        cmd += ["-c", f"http.extraHeader={_auth_header(token)}"]
    cmd += ["clone", "--branch", branch or "main", "--single-branch", "--depth", "1", repo_url, str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_CLONE_TIMEOUT_S, env=env)
    finally:
        if keyfile is not None:
            try:
                os.unlink(keyfile.name)
            except OSError:
                pass
    if proc.returncode != 0:
        raise RuntimeError(f"git clone falhou: {proc.stderr.strip()[-500:] or proc.stdout.strip()[-500:]}")


def _init_local(dest: Path, branch: str) -> None:
    """Projeto SEM remoto: só um `git init` vazio — o usuário/a IA populam os
    arquivos do zero. Nunca reclona/descarta (não há origem pra puxar de novo)."""
    if dest.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "init", "-b", branch or "main", str(dest)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"git init falhou: {(proc.stderr or proc.stdout).strip()[-400:]}")


def _prepare_folder(dest: Path, branch: str) -> None:
    """source='folder': usa um diretório EXISTENTE do host (estilo "abrir pasta no
    VSCode"). Faz `git init` se ainda não for repo (sem apagar nada) e garante ao
    menos 1 commit — os worktrees precisam de uma base. Nunca reclona/descarta."""
    if not dest.exists() or not dest.is_dir():
        raise RuntimeError(f"pasta não encontrada no servidor: {dest}")
    if not (dest / ".git").exists():
        proc = subprocess.run(["git", "init", "-b", branch or "main", str(dest)],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"git init falhou: {(proc.stderr or proc.stdout).strip()[-400:]}")
    if _git(dest, "rev-parse", "--verify", "HEAD").returncode != 0:
        # repo sem nenhum commit → cria a base (stage tudo que já está na pasta)
        _git_commit(dest, "Codespace: commit inicial da pasta")


# --------------------------------------------------------------------------- #
# Indexação em background (clona/atualiza → CodeGraph.index() → persiste status)
# --------------------------------------------------------------------------- #
def spawn_index(project_id: uuid.UUID, reclone: bool = True) -> None:
    """Agenda indexação em background (fire-and-forget seguro).

    `reclone=True` (padrão — 1ª indexação, ou "Ressincronizar" explícito): apaga a
    working copy e clona de novo do zero — DESCARTA commits locais não enviados
    (inclusive os que a IA fez). `reclone=False` ("Reindexar" normal): reaproveita
    a working copy atual, só reroda o índice (incremental, por content-hash) —
    seguro de chamar a qualquer momento, nunca perde trabalho da IA."""
    import asyncio

    t = asyncio.create_task(_index_project(project_id, reclone))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def _mark(Session, project_id: uuid.UUID, **fields: Any) -> None:
    async with Session() as db:
        p = await db.get(CodespaceProject, project_id)
        if p is None:
            return
        for k, v in fields.items():
            setattr(p, k, v)
        await db.commit()


_UNSUPPORTED_SAMPLE = 6


def unsupported_extensions(root: Path, limit: int = _UNSUPPORTED_SAMPLE) -> list[str]:
    """Extensões presentes na working copy que o grafo NÃO reconhece.

    Só é calculado quando a indexação termina com ZERO arquivos: sem isso o
    painel mostra um "0 arquivos" mudo e um projeto de .html/.css parece um
    reindex quebrado, quando na verdade essas extensões não têm gramática no
    GraphCodeMap. Os arquivos continuam legíveis/pesquisáveis pelas tools."""
    from codegraph.languages import language_for

    counts: Counter[str] = Counter()
    for p in root.rglob("*"):
        if ".git" in p.parts or not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext and language_for(p.name) is None:
            counts[ext] += 1
    return [ext for ext, _ in counts.most_common(limit)]


async def _index_project(project_id: uuid.UUID, reclone: bool = True) -> None:
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            proj = await db.get(CodespaceProject, project_id)
            if proj is None:
                return
            user_id = str(proj.user_id)
            repo_url, branch, source = proj.repo_url, proj.branch, proj.source
            gh_account_id = str(proj.github_account_id) if proj.github_account_id else None
            ssh_key = proj.ssh_private_key
            register_folder(proj)  # source='folder' → working_copy_path aponta p/ local_path

        dest = working_copy_path(user_id, str(project_id))
        # "local" nunca reclona (não há origem pra puxar de novo) — só inicializa
        # uma vez; "folder" sempre roda o preparo idempotente (init/commit se preciso).
        if source == "local":
            need_clone = not dest.exists()
        elif source == "folder":
            need_clone = True
        else:
            need_clone = reclone or not dest.exists()

        if need_clone:
            await _mark(Session, project_id, index_status="cloning", error_message=None)
            try:
                if source == "local":
                    await run_in_threadpool(_init_local, dest, branch)
                elif source == "folder":
                    await run_in_threadpool(_prepare_folder, dest, branch)
                elif source == "git-ssh":
                    if not ssh_key:
                        raise RuntimeError("projeto sem deploy key gerada")
                    await run_in_threadpool(_clone_or_refresh, repo_url, branch, dest, None, ssh_key)
                elif source == "git":
                    token = None
                    if gh_account_id:
                        from ..integrations import github_service

                        token = await github_service.get_token(gh_account_id)
                    await run_in_threadpool(_clone_or_refresh, repo_url, branch, dest, token)
                else:
                    raise RuntimeError(f"origem '{source}' desconhecida")
            except Exception as exc:  # noqa: BLE001
                logger.warning("codespace clone falhou (%s): %s", project_id, exc)
                await _mark(Session, project_id, index_status="error", error_message=str(exc)[:1000])
                return
            invalidate(str(project_id))  # working copy nova — reabre do zero

        await _mark(Session, project_id, index_status="indexing", error_message=None)

        try:
            t0 = time.time()
            cg = await run_in_threadpool(_get_graph, user_id, str(project_id))
            # force=True só numa working copy RECÉM-clonada (tudo "novo" p/ o
            # indexador); caso contrário força incremental (content-hash) — mais
            # rápido e é o que faz o reindex pós-escrita da IA ser barato.
            # `exclude`: o deny do escopo vira política de indexação persistida
            # no índice (API de host do GraphCodeMap) — símbolo de arquivo negado
            # NUNCA entra no grafo (antes o deny valia p/ arquivos mas find/
            # references vazavam assinatura+docstring). Lista vazia LIMPA a
            # política salva (deny removido → arquivo volta ao grafo).
            async with Session() as db:
                proj2 = await db.get(CodespaceProject, project_id)
                deny = [str(p) for p in ((proj2.scope or {}).get("deny") or [])] if proj2 else []
            await run_in_threadpool(lambda: cg.index(need_clone, exclude=deny))
            elapsed = round(time.time() - t0, 2)
            stats = await run_in_threadpool(cg.stats)
            stats["index_seconds"] = elapsed
            stats["refined"] = False
            if not stats.get("files"):
                stats["unsupported_ext"] = await run_in_threadpool(unsupported_extensions, dest)
        except Exception as exc:  # noqa: BLE001
            logger.warning("codespace index falhou (%s): %s", project_id, exc)
            await _mark(Session, project_id, index_status="error", error_message=str(exc)[:1000])
            return

        await _mark(
            Session, project_id,
            index_status="ready", stats=stats, last_indexed_at=datetime.now(timezone.utc),
        )
    finally:
        await eng.dispose()


def spawn_refine(project_id: uuid.UUID, user_id: str) -> None:
    """Resolução L1 (jedi p/ Python): promove arestas 'inferred'/'possible' a
    'certain'. Fica FORA do fluxo padrão de indexação — é caro (dezenas de
    segundos mesmo em repos pequenos) — o usuário aciona quando quiser precisão
    maior nas respostas de callers/impact."""
    import asyncio

    t = asyncio.create_task(_refine_project(project_id, user_id))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def _refine_project(project_id: uuid.UUID, user_id: str) -> None:
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        try:
            from codegraph import Indexer, l1

            root = working_copy_path(user_id, str(project_id))
            db_path = _db_path(user_id, str(project_id))
            ix = await run_in_threadpool(Indexer, str(root), str(db_path))
            r = await run_in_threadpool(l1.refine, ix)
            invalidate(str(project_id))  # próxima query relê o db já refinado
            cg = await run_in_threadpool(_get_graph, user_id, str(project_id))
            stats = await run_in_threadpool(cg.stats)
            stats["refined"] = True
            stats["refine_promoted"] = r.get("promoted", 0)
            await _mark(Session, project_id, stats=stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning("codespace refine falhou (%s): %s", project_id, exc)
    finally:
        await eng.dispose()


async def load_project(user_id: str, project_id: str) -> CodespaceProject | None:
    """Carrega o projeto SÓ se pertence a `user_id` — chamado de dentro da tool
    (contexto síncrono) via `asyncio.run(...)`, igual a `_store_browser_shot` no
    browser. Defesa em profundidade: mesmo que um chat aponte (por engano ou
    manipulação da API) para um project_id de outro usuário, a tool nunca lê os
    arquivos/grafo de quem não é dono — a rota já valida isso ao vincular, mas a
    tool NÃO confia só nisso."""
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                pid = uuid.UUID(str(project_id))
            except ValueError:
                return None
            p = await db.get(CodespaceProject, pid)
            if p is None or str(p.user_id) != str(user_id):
                return None
            register_folder(p)
            return p
    finally:
        await eng.dispose()


def delete_project_files(user_id: str, project_id: str) -> None:
    """Remove o índice + worktrees do disco (chamado ao apagar o projeto). Para
    source='folder' isso apaga só `<proj>/` (graph.db + wt/) sob o codespace_data —
    NUNCA a pasta do usuário (local_path fica fora daqui)."""
    invalidate(project_id)
    _FOLDER_SRC.pop(str(project_id), None)
    d = _project_dir(user_id, project_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def load_project_ctx(user_id: str, project_id: str) -> tuple[CodespaceProject | None, bool]:
    """Como `load_project`, mas TAMBÉM devolve se `confirm_actions` está ligado
    (Configurações → Segurança) — usado pelas ações destrutivas/externas
    (delete/push) da tool de escrita, mesma engine efêmera/mesma sessão (1 round
    trip a mais, não 2 chamadas separadas)."""
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            try:
                pid = uuid.UUID(str(project_id))
                uid = uuid.UUID(str(user_id))
            except ValueError:
                return None, False
            p = await db.get(CodespaceProject, pid)
            if p is None or str(p.user_id) != str(user_id):
                return None, False
            u = await db.get(User, uid)
            confirm = bool(((u.profile or {}).get("security") or {}).get("confirm_actions", False)) if u else False
            register_folder(p)
            return p, confirm
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------- #
# Queries do grafo — moldadas p/ o modelo (compactas; confiança/avisos NUNCA somem)
# --------------------------------------------------------------------------- #
def _short(sym: dict | None) -> dict | None:
    if sym is None:
        return None
    sig = (sym.get("signature") or "").strip()
    if len(sig) > _MAX_SIG:
        sig = sig[:_MAX_SIG] + "…"
    doc = (sym.get("doc") or "").strip()
    return {
        "fqn": sym.get("fqn"), "kind": sym.get("kind"),
        "path": sym.get("path"), "line": sym.get("start_line"), "end_line": sym.get("end_line"),
        "signature": sig or None, "doc": (doc[:_MAX_DOC] or None) if doc else None,
    }


def find(user_id: str, project_id: str, query: str, limit: int = 10) -> dict:
    cg = _get_graph(user_id, project_id)
    symbols, env = cg.find_symbol(query, limit=min(max(limit, 1), _MAX_RESULTS))
    return {"symbols": [_short(s) for s in symbols], "warnings": env.warnings}


def callers(user_id: str, project_id: str, selector: str, depth: int = 1) -> dict:
    cg = _get_graph(user_id, project_id)
    target, edges, env = cg.callers(selector, depth=max(1, min(depth, 4)))
    return {
        "target": _short(target),
        "callers": [
            {
                "fqn": e.get("other_fqn"), "path": e.get("site_path"), "line": e.get("line"),
                "confidence": e.get("confidence"), "depth": e.get("depth"),
            }
            for e in edges[:_MAX_RESULTS]
        ],
        "total_found": len(edges),
        "warnings": env.warnings,
    }


def impact(user_id: str, project_id: str, selector: str, depth: int = 3) -> dict:
    cg = _get_graph(user_id, project_id)
    target, edges, env = cg.impact(selector, depth=max(1, min(depth, 5)))
    return {
        "target": _short(target),
        "affects": [
            {
                "fqn": e.get("fqn"), "path": e.get("path"), "line": e.get("start_line"),
                "depth": e.get("depth"), "confidence": e.get("confidence"), "via": e.get("via"),
            }
            for e in edges[:_MAX_RESULTS]
        ],
        "total_found": len(edges),
        "warnings": env.warnings,
    }


def ego(user_id: str, project_id: str, selector: str) -> dict:
    cg = _get_graph(user_id, project_id)
    data, env = cg.ego_graph(selector)

    def _edge(e: dict) -> dict:
        return {
            "fqn": e.get("other_fqn") or e.get("dst_name"), "path": e.get("site_path"),
            "line": e.get("line"), "confidence": e.get("confidence"),
        }

    return {
        "symbol": _short(data.get("symbol")),
        "children": [
            {"name": c.get("name"), "kind": c.get("kind"), "line": c.get("start_line")}
            for c in (data.get("children") or [])[:_MAX_RESULTS]
        ],
        "calls": [_edge(e) for e in (data.get("out") or [])[:_MAX_RESULTS]],
        "called_by": [_edge(e) for e in (data.get("in") or [])[:_MAX_RESULTS]],
        "warnings": env.warnings,
    }


def status(user_id: str, project_id: str) -> dict:
    return _get_graph(user_id, project_id).stats()


_MAX_VIZ_NODES = 400


def _file_graph_fallback(conn, top: int) -> tuple[list[dict], list[dict]]:
    """Grafo por ARQUIVO que não depende de `edges.src`.

    A lib monta o nível de arquivo exigindo `edges.src` (símbolo de ORIGEM) e só
    vira nó quem tem aresta CRUZADA. Resolvers de nível 0 (ex.: HTML→CSS) gravam
    apenas `edges.file_id` (arquivo de origem) + `edges.dst` (símbolo alvo), com
    `src` NULL — então o grafo saía VAZIO ("Sem dados suficientes") mesmo com
    arquivos/símbolos/relações no índice. Aqui a origem vem de `e.file_id` e TODO
    arquivo entra como nó, então um projeto sem relação cruzada ainda se enxerga."""
    frows = conn.execute(
        "SELECT f.id, f.path, "
        "(SELECT community FROM symbols s WHERE s.file_id=f.id AND community IS NOT NULL "
        "   GROUP BY community ORDER BY COUNT(*) DESC LIMIT 1) AS domain, "
        "(SELECT COALESCE(SUM(rank),0) FROM symbols s WHERE s.file_id=f.id) AS weight, "
        "(SELECT COUNT(*) FROM symbols s WHERE s.file_id=f.id) AS nsyms "
        "FROM files f"
    ).fetchall()
    fmap = {r["id"]: dict(r) for r in frows}
    if not fmap:
        return [], []
    erows = conn.execute(
        "SELECT e.file_id AS a, s.file_id AS b, COUNT(*) AS w FROM edges e "
        "JOIN symbols s ON e.dst = s.id "
        "WHERE e.dst IS NOT NULL AND e.file_id != s.file_id "
        "GROUP BY e.file_id, s.file_id"
    ).fetchall()
    pair: dict[tuple[int, int], int] = {}
    degree: dict[int, int] = {}
    for r in erows:
        a, b = r["a"], r["b"]
        if a not in fmap or b not in fmap:
            continue
        key = (a, b) if a < b else (b, a)
        pair[key] = pair.get(key, 0) + r["w"]
        degree[a] = degree.get(a, 0) + r["w"]
        degree[b] = degree.get(b, 0) + r["w"]
    # mais conectados primeiro; os isolados entram depois (mas ENTRAM)
    keep = sorted(fmap, key=lambda i: (-degree.get(i, 0), fmap[i]["path"]))[:top]
    keep_set = set(keep)
    nodes = [
        {"id": i, "label": fmap[i]["path"], "domain": fmap[i]["domain"],
         "weight": fmap[i]["weight"], "n": fmap[i]["nsyms"]}
        for i in keep
    ]
    links = [
        {"source": a, "target": b, "w": w}
        for (a, b), w in pair.items() if a in keep_set and b in keep_set
    ]
    return nodes, links


# modos de visualização SEMEADOS por um símbolo (v0.1.0): vizinhança/chamadores/
# chamados/impacto partem de UM `symbol`; `domains` é o grafo entre comunidades.
_SEEDED_MODES = ("neighborhood", "callers", "callees", "impact")
_VIZ_MODES = _SEEDED_MODES + ("domains",)


def _norm_viz_node(nd: dict) -> dict:
    out = {
        "id": nd.get("id"),
        "label": nd.get("label") or nd.get("path") or nd.get("fqn") or str(nd.get("id")),
        "domain": nd.get("domain"),
        "n": nd.get("n") or nd.get("weight") or nd.get("size") or 1,
    }
    if nd.get("seed"):
        out["seed"] = True
    if nd.get("kind"):
        out["kind"] = nd.get("kind")
    return out


def _norm_viz_link(e: dict) -> dict:
    out = {"source": e.get("source"), "target": e.get("target"), "w": e.get("w") or e.get("weight") or 1}
    if e.get("confidence"):
        out["confidence"] = e.get("confidence")
    return out


def visualize(user_id: str, project_id: str, level: str = "file", scope: str = "",
              top: int = 200, mode: str = "", symbol: str = "",
              depth: int = 3, min_confidence: str = "", language: str = "") -> dict:
    """Grafo do projeto pra visualização estilo Obsidian. Dois eixos:

    - **nível** (`level`): `file` (padrão, agrega por arquivo — legível em repos
      grandes) ou `symbol` (função/classe a função/classe — "consultar por função").
    - **modo semeado** (`mode`, opcional, exige `symbol`): `neighborhood`,
      `callers`, `callees`, `impact` (subgrafo em volta de UM símbolo, à profundidade
      `depth`); ou `domains` (grafo entre comunidades). Ignora `level`.

    Filtros (v0.1.0): `min_confidence` (certain|inferred|possible — descarta arestas
    abaixo) e `language`. `top` limita nós (a lib já poda pelos mais conectados)."""
    cg = _get_graph(user_id, project_id)
    cap = min(max(top, 10), _MAX_VIZ_NODES)
    mc = min_confidence or None
    lang = language or None
    m = (mode or "").strip().lower()
    if m == "modules":
        m = "file"
    if m == "symbols":
        m = "symbol"

    if m in _VIZ_MODES:
        # modo investigativo semeado (ou o grafo de domínios)
        sym = (symbol or "").strip() or None
        if m in _SEEDED_MODES and not sym:
            return {"level": m, "nodes": [], "links": [], "domains": [],
                    "warnings": [f"o modo '{m}' precisa de um símbolo (função/classe) para partir"]}
        data, env = cg.visualize(m, symbol=sym, depth=max(1, min(depth, 5)), top=cap,
                                  min_confidence=mc, language=lang)
        return {
            "level": data.get("mode") or data.get("level") or m,
            "nodes": [_norm_viz_node(n) for n in (data.get("nodes") or [])],
            "links": [_norm_viz_link(e) for e in (data.get("links") or [])],
            "domains": data.get("domains") or [],
            "warnings": env.warnings,
        }

    lvl = level if level in ("file", "symbol") else "file"
    data, env = cg.visualize(level=lvl, scope=scope or None, top=cap,
                             min_confidence=mc, language=lang)
    nodes = data.get("nodes", [])
    links = data.get("links", [])
    domains = data.get("domains", [])
    # A lib zera o nível de arquivo quando as arestas não têm símbolo de ORIGEM
    # (`edges.src` NULL — resolvers de nível 0 como HTML→CSS) ou quando nenhum
    # arquivo tem aresta cruzada. Sem isto o usuário via "Sem dados suficientes"
    # com o painel dizendo "2 arquivos, 22 símbolos, 23 relações".
    if lvl == "file" and not nodes and not scope and not mc and not lang:
        try:
            nodes, links = _file_graph_fallback(cg.query.conn, cap)
            if nodes:
                from codegraph import viz as _viz
                domains = _viz._domain_legend(cg.query.conn, nodes)
        except Exception as exc:  # noqa: BLE001 - fallback é best-effort
            logger.warning("fallback do grafo por arquivo falhou: %s", exc)
    return {
        "level": data.get("level", lvl),
        "nodes": [_norm_viz_node(n) for n in nodes],
        "links": [_norm_viz_link(e) for e in links],
        "domains": domains,
        "warnings": env.warnings,
    }


# --------------------------------------------------------------------------- #
# Consultas avançadas do grafo — a GraphCodeMap faz bem mais que find/callers/
# impact/ego; estas são as que faltavam ter porta de entrada. Mesma disciplina
# das demais: saída COMPACTA (_short), teto de resultados e os avisos de
# completude/confiança sempre junto (nunca somem).
# --------------------------------------------------------------------------- #
_MAX_OVERVIEW_FILES = 60


def overview(user_id: str, project_id: str, scope: str = "", token_budget: int = 2000) -> dict:
    """Mapa do projeto já PODADO por relevância (rank) dentro de um orçamento de
    tokens — pensado p/ dar contexto inicial sem despejar a árvore inteira."""
    cg = _get_graph(user_id, project_id)
    files, env = cg.overview(
        scope=scope or None, token_budget=max(200, min(int(token_budget), 8000))
    )
    out = [
        {
            "path": f.get("path"),
            "symbols": [
                {
                    "fqn": s.get("fqn"), "kind": s.get("kind"), "line": s.get("start_line"),
                    "signature": ((s.get("signature") or "").strip()[:_MAX_SIG] or None),
                }
                for s in (f.get("symbols") or [])[:_MAX_RESULTS]
            ],
        }
        for f in (files or [])[:_MAX_OVERVIEW_FILES]
    ]
    return {"files": out, "total_files": len(files or []), "warnings": env.warnings}


def references(user_id: str, project_id: str, selector: str, kind: str = "") -> dict:
    """TODAS as referências a um símbolo (não só chamadas: importações etc.) —
    o "find all references" do editor. `kind` filtra (ex.: "calls", "imports")."""
    cg = _get_graph(user_id, project_id)
    target, refs, env = cg.references(selector, kind=kind or None)
    return {
        "target": _short(target),
        "references": [
            {
                "kind": r.get("kind"), "from": r.get("src_fqn"),
                "path": r.get("site_path"), "line": r.get("line"),
                "confidence": r.get("confidence"),
            }
            for r in (refs or [])[:_MAX_RESULTS]
        ],
        "total_found": len(refs or []),
        "warnings": env.warnings,
    }


def callees(user_id: str, project_id: str, selector: str, depth: int = 1) -> dict:
    """O inverso de `callers`: o que ESTE símbolo chama (transitivo até `depth`)."""
    cg = _get_graph(user_id, project_id)
    target, edges, env = cg.callees(selector, depth=max(1, min(depth, 4)))
    return {
        "target": _short(target),
        "callees": [
            {
                # não-resolvido: `other_fqn` é None mas `dst_name` tem o nome cru —
                # devolver o cru é melhor que devolver null (mesmo critério do ego)
                "fqn": e.get("other_fqn") or e.get("dst_name"),
                "path": e.get("site_path"), "line": e.get("line"),
                "confidence": e.get("confidence"), "depth": e.get("depth"),
            }
            for e in edges[:_MAX_RESULTS]
        ],
        "total_found": len(edges),
        "warnings": env.warnings,
    }


def symbol_info(user_id: str, project_id: str, selector: str) -> dict:
    """Ficha do símbolo: definição, filhos e quantos callers/callees/referências."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.symbol_info(selector)
    return {
        "symbol": _short(data.get("symbol")),
        "children": [
            {"name": c.get("name"), "kind": c.get("kind"), "line": c.get("start_line")}
            for c in (data.get("children") or [])[:_MAX_RESULTS]
        ],
        "counts": data.get("counts") or {},
        "domain": data.get("domain"),
        "warnings": env.warnings,
    }


def communities(user_id: str, project_id: str, limit: int = 20, min_size: int = 3) -> dict:
    """Módulos/agrupamentos detectados no grafo — "como este código se organiza"."""
    cg = _get_graph(user_id, project_id)
    rows, meta, env = cg.communities(
        limit=max(1, min(limit, 50)), min_size=max(2, min(min_size, 20))
    )
    return {
        "communities": [
            {
                "id": c.get("id"), "size": c.get("size"),
                "label": c.get("label"), "summary": c.get("summary"),
                "top_symbols": [s.get("fqn") for s in (c.get("top_symbols") or [])[:10]],
                "top_files": [f.get("path") for f in (c.get("top_files") or [])[:10]],
            }
            for c in (rows or [])
        ],
        "meta": meta or {},
        "warnings": env.warnings,
    }


def change_impact(user_id: str, project_id: str, target: str, depth: int = 3) -> dict:
    """Impacto de um CONJUNTO de mudanças — recebe PATHS ou um DIFF (não um fqn):
    quais símbolos declarados nos arquivos mudados têm dependentes, e o fecho
    transitivo deles (o que revisar/re-testar). Orientado ao diff real do agente."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.change_impact(target, depth=max(1, min(depth, 5)))
    return {
        "changed_files": (data.get("changed_files") or [])[:_MAX_RESULTS],
        "changed_symbols": [
            {"fqn": s.get("fqn"), "path": s.get("path"), "line": s.get("start_line")}
            for s in (data.get("changed_symbols") or [])[:_MAX_RESULTS]
        ],
        "impacted": [
            {
                "fqn": e.get("fqn"), "path": e.get("path"), "line": e.get("start_line"),
                "depth": e.get("depth"), "confidence": e.get("confidence"), "via": e.get("via"),
            }
            for e in (data.get("impacted") or [])[:_MAX_RESULTS]
        ],
        "n_changed": data.get("n_changed"),
        "n_impacted": data.get("n_impacted"),
        "warnings": env.warnings,
    }


def affected_modules(user_id: str, project_id: str, target: str, depth: int = 3) -> dict:
    """`change_impact` agregado por ARQUIVO/módulo: quais módulos uma mudança toca
    e com que profundidade — visão de alto nível p/ o agente decidir o que abrir."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.find_affected_modules(target, depth=max(1, min(depth, 5)))
    return {
        "changed_files": (data.get("changed_files") or [])[:_MAX_RESULTS],
        "modules": [
            {
                "path": m.get("path"), "count": m.get("count"),
                "min_depth": m.get("min_depth"), "symbols": (m.get("symbols") or [])[:6],
            }
            for m in (data.get("modules") or [])[:_MAX_RESULTS]
        ],
        "n_modules": data.get("n_modules"),
        "warnings": env.warnings,
    }


def related_tests(user_id: str, project_id: str, selector: str, depth: int = 3) -> dict:
    """Testes que exercitam um símbolo: callers transitivos que moram em arquivos
    de teste (test_*, *_test, *Spec…) — heurística sobre o call graph, "o que já
    cobre isto hoje". Confiança sempre junto (é estática)."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.find_related_tests(selector, depth=max(1, min(depth, 4)))
    return {
        "symbol": _short(data.get("symbol")),
        "tests": [
            {
                "test": t.get("test"), "path": t.get("path"), "line": t.get("line"),
                "depth": t.get("depth"), "confidence": t.get("confidence"),
            }
            for t in (data.get("tests") or [])[:_MAX_RESULTS]
        ],
        "n": data.get("n"),
        "warnings": env.warnings,
    }


def explain(user_id: str, project_id: str, selector: str) -> dict:
    """Ficha rica de um símbolo p/ decidir SEM reler o código: assinatura/doc/span
    + contagens + vizinhança imediata (top callers/callees c/ confiança) + domínio.
    Sem custo de LLM (é o `info` turbinado)."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.explain_symbol(selector)
    return {
        "symbol": _short(data.get("symbol")),
        "children": [
            {"name": c.get("name"), "kind": c.get("kind"), "line": c.get("start_line")}
            for c in (data.get("children") or [])[:_MAX_RESULTS]
        ],
        "counts": data.get("counts") or {},
        "domain": data.get("domain"),
        "callers": [
            {"fqn": e.get("fqn"), "confidence": e.get("confidence")}
            for e in (data.get("callers") or [])[:_MAX_RESULTS]
        ],
        "callees": [
            {"fqn": e.get("fqn"), "confidence": e.get("confidence")}
            for e in (data.get("callees") or [])[:_MAX_RESULTS]
        ],
        "warnings": env.warnings,
    }


def suggest_files(user_id: str, project_id: str, task: str, limit: int = 8) -> dict:
    """Arquivos mais relevantes p/ uma TAREFA em linguagem natural: extrai termos,
    casa símbolos e ranqueia por importância no grafo (PageRank) + nº de casamentos.
    Ponto de partida do agente num projeto desconhecido — 'por onde começo pra X'."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.suggest_files_to_read(task, limit=max(1, min(limit, 20)))
    return {
        "task": data.get("task"),
        "tokens": data.get("tokens") or [],
        "files": [
            {"path": f.get("path"), "score": f.get("score"), "matches": (f.get("matches") or [])[:5]}
            for f in (data.get("files") or [])[:_MAX_RESULTS]
        ],
        "warnings": env.warnings,
    }


def doctor(user_id: str, project_id: str, failed_limit: int = 20) -> dict:
    """Saúde do índice: arquivos que falharam no parse, % de arestas certeiras,
    idade do último scan. Responde "por que não acho X?" com dado, não achismo."""
    cg = _get_graph(user_id, project_id)
    out = dict(cg.doctor(failed_limit=max(1, min(failed_limit, 100))) or {})
    # `root` é o caminho ABSOLUTO no servidor (/data/codespace/<uid>/<pid>/src):
    # não interessa ao modelo e expõe o layout do disco — sai.
    out.pop("root", None)
    # epoch cru: o modelo não sabe que horas são no servidor; a idade em segundos
    # (`last_full_scan_age_s`, que a lib já dá ao lado) responde a mesma pergunta.
    out.pop("last_full_scan", None)
    return out


# --------------------------------------------------------------------------- #
# Fluxo de dados / taint — análise de SEGURANÇA (para onde um valor vai, o que
# alcança um sink, quais entradas contaminam o quê). Estática e "may-taint"
# (over-aproxima), por isso os avisos da lib são repassados sem filtro: são
# candidatos a verificar, não veredito.
# --------------------------------------------------------------------------- #
def data_flow(user_id: str, project_id: str, selector: str, depth: int = 2) -> dict:
    """Para onde vão os parâmetros desta função (retorno e/ou sinks chamados)."""
    cg = _get_graph(user_id, project_id)
    data, env = cg.data_flow(selector, depth=max(1, min(depth, 5)))
    return {
        "function": _short(data.get("function")),
        "supported": data.get("supported"),
        "params": [
            {
                "name": p.get("name"),
                "reaches_return": p.get("reaches_return"),
                "sinks": [
                    {
                        "callee": s.get("callee_fqn") or s.get("callee_name"),
                        "path": s.get("site_path"), "line": s.get("line"),
                        "via": s.get("via"), "depth": s.get("depth"),
                        "confidence": s.get("confidence"),
                    }
                    for s in (p.get("sinks") or [])[:_MAX_RESULTS]
                ],
            }
            for p in (data.get("params") or [])[:_MAX_RESULTS]
        ],
        "warnings": env.warnings,
    }


def reaches(user_id: str, project_id: str, selector: str, sink: str = "http",
            via: str = "", depth: int = 8) -> dict:
    """Este símbolo alcança um `sink` (http, db, fs…)? Devolve as CADEIAS de
    chamada que levam até lá — a confiança é a MÍNIMA do caminho."""
    cg = _get_graph(user_id, project_id)
    # depth 12 enumerava caminhos demais em grafos grandes (blowup como o da taint);
    # 6 já cobre cadeias reais e termina rápido. Watchdog do dispatcher é a rede final.
    target, data, env = cg.reaches(
        selector, sink=sink or "http", via=via or None, depth=max(1, min(depth, 6))
    )
    paths = data.get("paths") or []
    return {
        "target": _short(target),
        "sink": data.get("sink"),
        "via": data.get("via"),
        "paths": [
            {
                "chain": p.get("chain"), "sink_call": p.get("sink_call"),
                "path": p.get("site_path"), "line": p.get("line"),
                "confidence": p.get("confidence"), "via_present": p.get("via_present"),
            }
            for p in paths[:_MAX_RESULTS]
        ],
        "total_found": len(paths),
        "warnings": env.warnings,
    }


def taint(user_id: str, project_id: str, scope: str = "", entry: str = "",
          depth: int = 4) -> dict:
    """Varredura de taint: entradas não confiáveis que chegam a sinks perigosos.
    Sem `entry` varre o projeto; com `entry` parte de uma função específica.
    Regras customizáveis pelo usuário em `.codegraph/taint.json` do projeto."""
    cg = _get_graph(user_id, project_id)
    # BOUND anti-explosão: o modo VARREDURA (sem entry) roda um trace interprocedural a
    # partir de TODA função do escopo — depth alto sobre uma base grande explode
    # (custo ~ funções × ramificação^depth) e pendurava o turno. Com `entry` (focado,
    # 1 função) o custo é baixo, então permitimos mais fundo. O watchdog do dispatcher
    # (builtin_tool_timeout_seconds) é a rede final; aqui evitamos CHEGAR nela.
    _entry = entry or None
    _cap = 6 if _entry else 3
    eff_depth = max(1, min(depth, _cap))
    data, env = cg.taint(scope=scope or None, entry=_entry, depth=eff_depth)
    findings = data.get("findings") or []
    warns = list(env.warnings)
    if not _entry:
        warns.append(
            "modo varredura (sem `entry`): profundidade limitada a "
            f"{eff_depth} p/ não explodir numa base grande. Para ir mais fundo numa "
            "cadeia específica, chame de novo com `entry=<função>` (rápido e preciso) "
            "ou reduza o `scope` a um diretório/arquivo."
        )
    return {
        "mode": data.get("mode"),
        "findings": findings[:_MAX_RESULTS],
        "scanned": data.get("scanned"),
        "total_found": len(findings),
        "warnings": warns,
    }


# --------------------------------------------------------------------------- #
# Arquivos — path-jail + escopo allow/deny (globs estilo .gitignore via pathspec)
# --------------------------------------------------------------------------- #
_DEFAULT_DENY = [".git/", "__pycache__/", "*.pyc", "node_modules/", ".codegraph/"]


def _spec(patterns: list[str]) -> pathspec.PathSpec | None:
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns) if patterns else None


def safe_path(root: Path, rel: str) -> Path:
    """Resolve `rel` DENTRO de `root`; recusa `..`, absoluto ou escape via symlink."""
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    root_r = root.resolve()
    candidate = (root_r / rel).resolve() if rel else root_r
    if candidate != root_r and root_r not in candidate.parents:
        raise ValueError("caminho fora do projeto")
    return candidate


def _rel(root: Path, path: Path) -> str:
    root_r = root.resolve()
    return "." if path == root_r else path.relative_to(root_r).as_posix()


def is_allowed(root: Path, path: Path, scope: dict | None) -> bool:
    scope = scope or {}
    deny_spec = _spec((scope.get("deny") or []) + _DEFAULT_DENY)
    allow_spec = _spec(scope.get("allow") or [])
    rel = _rel(root, path)
    # patterns de diretório ("x/") só casam contra um path que TERMINA em "/" —
    # sem isso, ".git/" nunca bate com a entrada ".git" nem com ".git/config".
    match_rel = f"{rel}/" if path.is_dir() and rel != "." else rel
    if deny_spec and deny_spec.match_file(match_rel):
        return False
    if allow_spec and rel != "." and not allow_spec.match_file(match_rel):
        return False
    return True


def list_files(user_id: str, project_id: str, scope: dict | None, path: str = "", max_depth: int = 3) -> dict:
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não indexado/clonado"}
    start = safe_path(root, path)
    if not start.exists():
        return {"error": f"caminho não encontrado: {path}"}
    root_r = root.resolve()
    base_depth = len(start.relative_to(root_r).parts) if start != root_r else 0
    out: list[dict] = []
    truncated = False

    def _walk(d: Path) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for p in children:
            # poda ANTES de descer: um diretório negado (.git, node_modules…)
            # nunca é caminhado por dentro — não só filtrado depois.
            if not is_allowed(root, p, scope):
                continue
            out.append({"path": _rel(root, p), "kind": "dir" if p.is_dir() else "file"})
            if len(out) >= _MAX_LIST_ENTRIES:
                truncated = True
                return
            if p.is_dir():
                depth = len(p.relative_to(root_r).parts) - base_depth
                if depth < max_depth:
                    _walk(p)
                    if truncated:
                        return

    _walk(start)
    if truncated:
        out.append({"note": f"truncado em {_MAX_LIST_ENTRIES} entradas — refine o `path`"})
    return {"entries": out}


def read_file(user_id: str, project_id: str, scope: dict | None, path: str,
              start_line: int = 1, end_line: int | None = None) -> dict:
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não indexado/clonado"}
    target = safe_path(root, path)
    if not target.is_file():
        return {"error": f"arquivo não encontrado: {path}"}
    if not is_allowed(root, target, scope):
        return {"error": "arquivo fora do escopo liberado deste projeto"}
    if target.stat().st_size > _MAX_FILE_BYTES:
        return {"error": f"arquivo grande demais ({target.stat().st_size} bytes) — peça um trecho por range"}
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    lo = max(1, start_line)
    hi = min(len(lines), end_line or (lo + _MAX_FILE_LINES - 1), lo + _MAX_FILE_LINES - 1)
    numbered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(lo, hi + 1))
    return {
        "path": _rel(root, target), "total_lines": len(lines),
        "start_line": lo, "end_line": hi, "content": numbered,
    }


_TEXT_EXTS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".cs", ".c", ".h",
    ".cpp", ".hpp", ".php", ".rb", ".lua", ".swift", ".scala", ".clj", ".md", ".txt",
    ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh", ".sql", ".html", ".css",
)


_SEARCH_TIMEOUT_S = 30
_MAX_LINE_CHARS = 300


def _rg_search(root: Path, scope: dict | None, query: str, glob: str, regex: bool,
               case_sensitive: bool, context: int, files_only: bool, cap: int) -> dict:
    """Busca via ripgrep (`rg --json`). Regex de verdade, detecção de binário e
    velocidade — o que a varredura em Python não dava. `FileNotFoundError` sobe
    p/ o chamador cair no fallback quando o binário não existe."""
    cmd = [
        "rg", "--json", "--hidden",   # --hidden: acha .github/workflows, .config…
        "--max-filesize", str(_MAX_FILE_BYTES),
        # o .git é grande e inútil aqui; o resto do escopo vira --glob abaixo
        "--glob", "!.git/",
    ]
    if not regex:
        cmd.append("--fixed-strings")
    if not case_sensitive:
        cmd.append("--ignore-case")
    if files_only:
        cmd += ["--max-count", "1"]   # 1 hit por arquivo basta p/ listar arquivos
    elif context > 0:
        cmd += ["--context", str(min(context, 5))]
    for pat in (scope or {}).get("deny") or []:
        cmd += ["--glob", f"!{pat}"]
    for pat in (scope or {}).get("allow") or []:
        cmd += ["--glob", pat]
    if glob:
        cmd += ["--glob", glob]
    # `--` ANTES do padrão: sem isso um query começando com "-" é lido como FLAG
    # pelo rg (mesma classe de injeção de argumento já corrigida no git_diff).
    cmd += ["--", query, str(root)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_SEARCH_TIMEOUT_S)
    # rg: 0 = achou, 1 = nada encontrado, 2 = erro real (regex inválida etc.)
    if proc.returncode == 2:
        return {"error": (proc.stderr or "busca falhou").strip()[:300]}

    results: list[dict] = []
    files: list[str] = []
    seen: set[str] = set()
    truncated = False
    for line in proc.stdout.splitlines():
        if len(results) >= cap or len(files) >= cap:
            truncated = True
            break
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind not in ("match", "context"):
            continue
        data = ev.get("data") or {}
        path_txt = ((data.get("path") or {}).get("text")) or ""
        if not path_txt:
            continue
        target = Path(path_txt)
        # defesa em profundidade: os --glob acima já filtram, mas o veredito de
        # escopo continua sendo o MESMO `is_allowed` do resto do serviço
        if not is_allowed(root, target, scope):
            continue
        rel = _rel(root, target)
        if files_only:
            if rel not in seen:
                seen.add(rel)
                files.append(rel)
            continue
        results.append({
            "path": rel,
            "line": data.get("line_number"),
            "text": ((data.get("lines") or {}).get("text") or "").rstrip("\n")[:_MAX_LINE_CHARS],
            **({"context": True} if kind == "context" else {}),
        })

    if files_only:
        return {"files": files, "truncated": truncated}
    return {"results": results, "truncated": truncated}


def _py_search(root: Path, scope: dict | None, query: str, glob: str, regex: bool,
               case_sensitive: bool, cap: int) -> dict:
    """Fallback sem ripgrep: varredura em Python. Mais fraca de propósito — só
    percorre extensões conhecidas (`_TEXT_EXTS`), sem contexto nem files_only —
    existe para o serviço não quebrar num ambiente sem o binário."""
    try:
        pattern = re.compile(query if regex else re.escape(query),
                             0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        return {"error": f"expressão inválida: {exc}"}
    glob_spec = _spec([glob]) if glob else None
    results: list[dict] = []

    def _walk(d: Path) -> None:
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for p in children:
            if len(results) >= cap:
                return
            # poda diretórios negados (.git, node_modules…) ANTES de descer —
            # evita vasculhar objetos git/binários à toa num repo grande.
            if not is_allowed(root, p, scope):
                continue
            if p.is_dir():
                _walk(p)
                continue
            if p.suffix.lower() not in _TEXT_EXTS:
                continue
            rel = _rel(root, p)
            if glob_spec and not glob_spec.match_file(rel):
                continue
            try:
                if p.stat().st_size > _MAX_FILE_BYTES:
                    continue
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    if pattern.search(line):
                        results.append({"path": rel, "line": i, "text": line.strip()[:_MAX_LINE_CHARS]})
                        if len(results) >= cap:
                            return
            except OSError:
                continue

    _walk(root)
    return {"results": results, "truncated": len(results) >= cap}


def search_files(user_id: str, project_id: str, scope: dict | None, query: str,
                  glob: str = "", regex: bool = False, case_sensitive: bool = False,
                  context: int = 0, files_only: bool = False,
                  limit: int = _MAX_SEARCH_RESULTS) -> dict:
    """Busca por CONTEÚDO nos arquivos do projeto (o "grep" da IA), via ripgrep.

    `regex=False` (padrão) trata `query` como texto literal; `regex=True` usa a
    sintaxe do rust/regex. `files_only` devolve só a lista de arquivos que casam
    (bem mais barato quando a pergunta é "onde isso aparece?")."""
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não indexado/clonado"}
    if not (query or "").strip():
        return {"error": "`query` é obrigatório"}
    cap = max(1, min(int(limit or _MAX_SEARCH_RESULTS), _MAX_SEARCH_RESULTS))
    try:
        return _rg_search(root, scope, query, glob, regex, case_sensitive,
                          context, files_only, cap)
    except FileNotFoundError:
        logger.warning("ripgrep ausente — busca do Codespace em modo degradado (Python)")
    except subprocess.TimeoutExpired:
        return {"error": f"a busca passou de {_SEARCH_TIMEOUT_S}s — restrinja com `glob` ou um termo mais específico"}
    return _py_search(root, scope, query, glob, regex, case_sensitive, cap)


# --------------------------------------------------------------------------- #
# Escrita + git — toda escrita vira um commit LOCAL automático (reversível, sem
# fricção); push é ação SEPARADA e explícita (afeta o remoto, menos reversível).
# --------------------------------------------------------------------------- #
_GIT_AUTHOR = ("Codespace AI", "codespace-ai@ai-workspace.local")


def _git(root: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)


def _git_commit(root: Path, message: str) -> dict | None:
    """`git add -A` + commit (identidade via `-c`, NÃO persiste em `.git/config`).
    Devolve {sha, message} ou None se não havia nada pra commitar (working tree
    já limpa — ex.: `write` com o MESMO conteúdo que já existia)."""
    name, email = _GIT_AUTHOR
    _git(root, "add", "-A")
    proc = _git(root, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", message[:500])
    if proc.returncode != 0:
        if "nothing to commit" in (proc.stdout + proc.stderr).lower():
            return None
        raise RuntimeError(f"git commit falhou: {(proc.stderr or proc.stdout).strip()[-400:]}")
    sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"sha": sha, "message": message}


def _reindex_after_write(user_id: str, project_id: str) -> dict | None:
    """Reindexação SÍNCRONA e INCREMENTAL (content-hash) logo após um write/edit/
    delete — sem isso, uma query de grafo NO MESMO turno veria dados velhos.
    Falha aqui não derruba o resultado da escrita: o arquivo já foi salvo e
    commitado: o pior caso é o grafo ficar 1 chamada atrasado.

    Devolve `changes` do índice (added/removed/signature_changed — API de host
    do GraphCodeMap): a tool repassa ao modelo, que vê na hora "a assinatura de
    save_user mudou" e pode chamar `impact` ANTES de dar a tarefa por encerrada
    — sem diff de git nem adivinhar símbolo. `exclude=None` mantém a política
    de deny persistida no índice."""
    try:
        cg = _get_graph(user_id, project_id)
        out = cg.index()  # force=False: só reparsa o que mudou (rápido)
        ch = out.get("changes") if isinstance(out, dict) else None
        if isinstance(ch, dict) and any(
            ch.get(k) for k in ("added", "removed", "signature_changed")
        ):
            return ch
        return None
    except Exception:  # noqa: BLE001
        logger.warning("reindex pós-escrita falhou (%s/%s)", user_id, project_id, exc_info=True)
        return None


def write_file(user_id: str, project_id: str, scope: dict | None, path: str,
                content: str, message: str = "", root: Path | None = None,
                reindex: bool = True) -> dict:
    # `root` != None → escrita num WORKTREE (tarefa isolada): commita na branch do
    # worktree e NÃO reindexa o grafo do projeto (o grafo reflete o `src`; o worktree
    # só entra no grafo ao ser mesclado).
    root = root or working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    target = safe_path(root, path)
    if not is_allowed(root, target, scope):
        return {"error": "caminho fora do escopo liberado deste projeto"}
    if len(content.encode("utf-8", errors="replace")) > _MAX_FILE_BYTES:
        return {"error": f"conteúdo grande demais (máx {_MAX_FILE_BYTES} bytes) — escreva em partes menores"}
    is_new = not target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rel = _rel(root, target)
    commit = _git_commit(root, message.strip() or f"AI: {'cria' if is_new else 'atualiza'} {rel}")
    changes = _reindex_after_write(user_id, project_id) if reindex else None
    out = {"ok": True, "path": rel, "created": is_new, "commit": commit}
    if changes:
        out["symbol_changes"] = changes
    return out


def edit_file(user_id: str, project_id: str, scope: dict | None, path: str,
              search: str, replace: str, message: str = "", root: Path | None = None,
              reindex: bool = True) -> dict:
    """SEARCH/REPLACE: `search` precisa bater EXATAMENTE (copiado do `read`) e
    ser ÚNICO no arquivo — mesma disciplina do `<artifact-edit>` do chat, evita
    o modelo trocar o trecho errado por ambiguidade. `root`/`reindex`: ver write_file."""
    root = root or working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    target = safe_path(root, path)
    if not target.is_file():
        return {"error": f"arquivo não encontrado: {path}"}
    if not is_allowed(root, target, scope):
        return {"error": "caminho fora do escopo liberado deste projeto"}
    if not search:
        return {"error": "`search` é obrigatório"}
    text = target.read_text(encoding="utf-8", errors="replace")
    count = text.count(search)
    if count == 0:
        return {"error": "texto de `search` não encontrado — copie exatamente do `read` (indentação inclusa)"}
    if count > 1:
        return {"error": f"texto de `search` aparece {count} vezes — inclua mais contexto para ficar único"}
    target.write_text(text.replace(search, replace, 1), encoding="utf-8")
    rel = _rel(root, target)
    commit = _git_commit(root, message.strip() or f"AI: edita {rel}")
    changes = _reindex_after_write(user_id, project_id) if reindex else None
    out = {"ok": True, "path": rel, "commit": commit}
    if changes:
        out["symbol_changes"] = changes
    return out


def _paths_from_diff(diff: str) -> set[str]:
    """Extrai os arquivos afetados de um diff unificado (linhas '--- a/'/'+++ b/'),
    tirando o prefixo a/ b/, /dev/null e timestamp pós-tab — p/ validar escopo."""
    paths: set[str] = set()
    for line in diff.splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        p = line[4:].split("\t")[0].strip()
        if not p or p == "/dev/null":
            continue
        if p[:2] in ("a/", "b/"):
            p = p[2:]
        if p:
            paths.add(p)
    return paths


def apply_patch(user_id: str, project_id: str, scope: dict | None, diff: str,
                message: str = "", root: Path | None = None, reindex: bool = True) -> dict:
    """Aplica um DIFF unificado (formato `git diff`) ao projeto — várias mudanças e
    arquivos numa só chamada, mais robusto e barato em tokens que N edits SEARCH/REPLACE.
    Usa `git apply` (valida com --check antes; tenta --3way se o contexto deslocou).
    `root`/`reindex`: ver write_file."""
    import os
    import tempfile

    root = root or working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    diff = diff or ""
    if not diff.strip():
        return {"error": "`diff` vazio — passe um diff unificado no formato `git diff`"}
    if not diff.endswith("\n"):
        diff += "\n"
    if len(diff.encode("utf-8", "replace")) > _MAX_FILE_BYTES:
        return {"error": f"diff grande demais (máx {_MAX_FILE_BYTES} bytes) — divida em partes"}
    paths = _paths_from_diff(diff)
    if not paths:
        return {"error": "não identifiquei arquivos no diff — inclua cabeçalhos '--- a/<path>' e '+++ b/<path>'"}
    for p in paths:
        if not is_allowed(root, safe_path(root, p), scope):
            return {"error": f"caminho fora do escopo liberado deste projeto: {p}"}
    fd, patch_path = tempfile.mkstemp(suffix=".patch")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(diff)
        chk = _git(root, "apply", "--check", "--whitespace=nowarn", patch_path)
        three_way = False
        if chk.returncode != 0:
            # contexto deslocado? tenta 3-way (usa os objetos do git p/ casar)
            if _git(root, "apply", "--check", "--3way", "--whitespace=nowarn", patch_path).returncode != 0:
                return {"error": "o diff não aplica limpo: "
                                 + (chk.stderr or chk.stdout or "").strip()[:400]
                                 + " — releia o arquivo com code.files.browse e gere o diff a partir do conteúdo ATUAL"}
            three_way = True
        args = ["apply", "--whitespace=nowarn", patch_path]
        if three_way:
            args.insert(1, "--3way")
        ap = _git(root, *args)
        if ap.returncode != 0:
            return {"error": "falha ao aplicar o diff: " + (ap.stderr or ap.stdout or "").strip()[:400]}
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass
    commit = _git_commit(root, message.strip() or f"AI: aplica patch ({len(paths)} arquivo(s))")
    changes = _reindex_after_write(user_id, project_id) if reindex else None
    out = {"ok": True, "paths": sorted(paths), "commit": commit}
    if changes:
        out["symbol_changes"] = changes
    return out


_MAX_FIND_RESULTS = 200


def find_files(user_id: str, project_id: str, scope: dict | None, query: str, limit: int = 100) -> dict:
    """Busca arquivos pelo NOME (substring, case-insensitive) — o explorador do
    chat/Espaço de Trabalho; diferente de `search_files` (busca por conteúdo)."""
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não indexado/clonado"}
    q = (query or "").strip().lower()
    if not q:
        return {"entries": []}
    cap = max(1, min(limit, _MAX_FIND_RESULTS))
    out: list[dict] = []

    def _walk(d: Path) -> None:
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for p in children:
            if len(out) >= cap:
                return
            if not is_allowed(root, p, scope):
                continue
            if p.is_dir():
                _walk(p)
                continue
            if q in p.name.lower():
                out.append({"path": _rel(root, p), "kind": "file"})

    _walk(root)
    return {"entries": out, "truncated": len(out) >= cap}


def move_file(user_id: str, project_id: str, scope: dict | None, path: str,
              dest_path: str, message: str = "") -> dict:
    """Mover/renomear (arraste-e-solte ou "Renomear" no explorador humano)."""
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    src = safe_path(root, path)
    if not src.exists():
        return {"error": f"caminho não encontrado: {path}"}
    dest = safe_path(root, dest_path)
    if not is_allowed(root, src, scope) or not is_allowed(root, dest, scope):
        return {"error": "caminho fora do escopo liberado deste projeto"}
    if dest.exists():
        return {"error": f"já existe um item em {dest_path}"}
    if src.is_dir():
        try:
            dest.relative_to(src)
            return {"error": "não é possível mover uma pasta para dentro dela mesma"}
        except ValueError:
            pass
    rel_src = _rel(root, src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    rel_dest = _rel(root, dest)
    commit = _git_commit(root, message.strip() or f"Move {rel_src} -> {rel_dest}")
    changes = _reindex_after_write(user_id, project_id)
    out = {"ok": True, "path": rel_dest, "commit": commit}
    if changes:
        out["symbol_changes"] = changes
    return out


def delete_file(user_id: str, project_id: str, scope: dict | None, path: str, message: str = "",
                root: Path | None = None, reindex: bool = True) -> dict:
    root = root or working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    target = safe_path(root, path)
    if not target.exists():
        return {"error": f"caminho não encontrado: {path}"}
    if not is_allowed(root, target, scope):
        return {"error": "caminho fora do escopo liberado deste projeto"}
    rel = _rel(root, target)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    commit = _git_commit(root, message.strip() or f"AI: remove {rel}")
    changes = _reindex_after_write(user_id, project_id) if reindex else None
    out = {"ok": True, "path": rel, "commit": commit}
    if changes:
        out["symbol_changes"] = changes
    return out


def git_log(user_id: str, project_id: str, limit: int = 20) -> dict:
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    proc = _git(root, "log", f"-{max(1, min(limit, 100))}", "--pretty=format:%h|%ad|%s", "--date=iso-strict")
    if proc.returncode != 0:
        return {"error": (proc.stderr or "git log falhou")[:300]}
    commits = []
    for line in proc.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "message": parts[2]})
    return {"commits": commits}


def git_diff(user_id: str, project_id: str, scope: dict | None, path: str = "", ref: str = "") -> dict:
    """`ref` vazio = mudanças não commitadas (normalmente vazio — cada escrita já
    commita); passe um sha/`HEAD~N` pra ver o que uma mudança específica fez."""
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    if ref.startswith("-"):
        # `ref` vira um argumento solto pro `git diff`: sem essa checagem, um
        # ref tipo "--output=/data/codespace/..." é lido como FLAG (não revisão)
        # e faz o git ESCREVER o diff em qualquer arquivo que o usuário `app`
        # consiga tocar — injeção de argumento, não só um ref inválido.
        return {"error": "ref inválido"}
    args = ["diff"] + ([ref] if ref else [])
    if path:
        target = safe_path(root, path)
        if not is_allowed(root, target, scope):
            return {"error": "caminho fora do escopo liberado deste projeto"}
        args += ["--", _rel(root, target)]
    proc = _git(root, *args)
    if proc.returncode != 0:
        return {"error": (proc.stderr or "git diff falhou")[:400]}
    diff = proc.stdout
    if len(diff) > _MAX_FILE_BYTES:
        diff = diff[:_MAX_FILE_BYTES] + "\n…[truncado]"
    return {"diff": diff or "(sem mudanças)"}


def _push(root: Path, branch: str, token: str | None, ssh_key: str | None = None) -> None:
    cmd = ["git", "-C", str(root)]
    env = None
    keyfile = None
    if ssh_key:
        env_extra, keyfile = _ssh_env(ssh_key)
        env = {**os.environ, **env_extra}
    elif token:
        cmd += ["-c", f"http.extraHeader={_auth_header(token)}"]
    cmd += ["push", "origin", f"HEAD:{branch}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_CLONE_TIMEOUT_S, env=env)
    finally:
        if keyfile is not None:
            try:
                os.unlink(keyfile.name)
            except OSError:
                pass
    if proc.returncode != 0:
        raise RuntimeError(f"git push falhou: {(proc.stderr or proc.stdout).strip()[-500:]}")


async def push(user_id: str, project_id: str) -> dict:
    """Envia os commits locais (feitos pela IA ou não) para o `origin`. Reusa o
    MESMO auth por header/deploy-key do clone (token/chave nunca tocam a
    URL/`.git/config`). Projeto "local" não tem origin — erro amigável."""
    proj = await load_project(user_id, project_id)
    if proj is None:
        return {"error": "projeto não encontrado (ou não pertence a este usuário)"}
    if proj.source == "local":
        return {"error": "projeto local não tem repositório remoto — nada para enviar"}
    root = working_copy_path(user_id, project_id)
    if not root.exists():
        return {"error": "projeto ainda não clonado"}
    token = None
    if proj.source == "git" and proj.github_account_id:
        from ..integrations import github_service

        token = await github_service.get_token(str(proj.github_account_id))
    try:
        await run_in_threadpool(_push, root, proj.branch, token, proj.ssh_private_key)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:400]}
    return {"ok": True, "branch": proj.branch}
