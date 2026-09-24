"""Painel do admin: gestão de usuários, configurações globais e backup."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__, audit_service, db_restore, network_config
from .app_config import ALLOW_SIGNUPS, get_setting, set_setting
from .auth.deps import require_admin
from .config import get_settings
from .crypto import (
    BACKUP_MAGIC, BACKUP_MAGIC_V2, BACKUP_MAGIC_V3, BACKUP_V3_HEADER, backup_decryptor,
    backup_encryptor, backup_password_decryptor, backup_password_encryptor, data_key_b64,
)
from .db import get_db
from .models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ConfigIn(BaseModel):
    allow_signups: bool


@router.get("/config")
async def get_config(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return {"allow_signups": bool(await get_setting(db, ALLOW_SIGNUPS, False))}


@router.put("/config")
async def put_config(
    body: ConfigIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    await set_setting(db, ALLOW_SIGNUPS, body.allow_signups)
    return {"ok": True, "allow_signups": body.allow_signups}


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(User).order_by(User.created_at))
    return list(rows)


async def _get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    return u


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
async def approve_user(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    u = await _get_user(db, user_id)
    u.status = "active"
    await db.commit()
    await db.refresh(u)
    return u


@router.post("/users/{user_id}/reject", response_model=AdminUserOut)
async def reject_user(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    u = await _get_user(db, user_id)
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode rejeitar a si mesmo")
    u.status = "rejected"
    await db.commit()
    await db.refresh(u)
    return u


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    u = await _get_user(db, user_id)
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode excluir a si mesmo")
    await db.delete(u)
    await db.commit()
    # as memórias não têm FK para users (o CASCADE não as alcança): apaga à parte
    from .memory import memory_service
    await run_in_threadpool(memory_service.delete_user_memories, str(user_id))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Rede (allowlist de IP em runtime; host/porta/repo p/ o deploy) + atualização
# --------------------------------------------------------------------------- #
class NetworkIn(BaseModel):
    host: str = Field(default="0.0.0.0", max_length=64)
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_ips: list[str] = Field(default_factory=list, max_length=200)
    repo: str = Field(default="", max_length=200)  # "owner/repo" p/ checar updates
    branch: str = Field(default="main", max_length=100)


def _same_commit(a: str | None, b: str | None) -> bool | None:
    """Dois hashes de commit apontam para o mesmo? Compara pelo PREFIXO comum, porque os
    lados têm comprimentos diferentes (a API do GitHub devolve o sha completo; a imagem
    guarda um short hash). None = desconhecido (sem um dos lados) — o chamador então não
    afirma nada sobre commits."""
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return None
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _normalize_repo(value: str) -> str:
    """Normaliza o repositório para `owner/repo` — o formato que a API do GitHub usa em
    /repos/{owner}/{repo}. Aceita o que o admin naturalmente cola: a URL do navegador
    (https://github.com/owner/repo), com ou sem `.git`, `git@github.com:owner/repo.git`,
    barras sobrando ou caminho extra (…/tree/main). Sem isto, colar a URL inteira montava
    `/repos/https://github.com/owner/repo/releases/latest` e devolvia 404 sem explicar."""
    v = (value or "").strip()
    if not v:
        return ""
    v = v.removeprefix("git@github.com:").removeprefix("ssh://git@github.com/")
    for pref in ("https://github.com/", "http://github.com/", "github.com/", "www.github.com/"):
        if v.lower().startswith(pref):
            v = v[len(pref):]
            break
    v = v.strip("/")
    if v.lower().endswith(".git"):
        v = v[:-4]
    parts = [p for p in v.split("/") if p]
    return "/".join(parts[:2]) if len(parts) >= 2 else v


@router.get("/network")
async def get_network(admin: User = Depends(require_admin)):
    cfg = await network_config.load_config()
    s = get_settings()
    return {
        "host": cfg.get("host", "0.0.0.0"),
        "port": cfg.get("port", 8000),
        "allowed_ips": network_config.get_allowlist(),
        "repo": cfg.get("repo", ""),
        "branch": cfg.get("branch", "main"),
        "trust_proxy": s.trust_proxy,
        "web_origin": s.web_origin,
    }


@router.put("/network")
async def put_network(body: NetworkIn, admin: User = Depends(require_admin)):
    # limpa/normaliza IPs (a validação real é no ipaddress do network_config)
    cfg = body.model_dump()
    cfg["allowed_ips"] = [i.strip() for i in cfg["allowed_ips"] if i and i.strip()][:200]
    cfg["repo"] = _normalize_repo(cfg["repo"])
    await network_config.save_config(cfg)
    return {"ok": True, **cfg, "allowed_ips": network_config.get_allowlist()}


async def _github_auth_header(db: AsyncSession, user_id) -> tuple[dict[str, str], bool]:
    """Cabeçalho de autorização usando a conta GitHub CONECTADA do admin (Integrações →
    GitHub). Necessário para repositório PRIVADO: sem token a API responde 404 (não 403 —
    o GitHub esconde a existência de repo privado), o que faz o check parecer "repo não
    encontrado". Devolve ({}, False) quando não há conta conectada."""
    from .integrations import github_service
    from .models import GithubAccount

    accs = list(await db.scalars(
        select(GithubAccount).where(GithubAccount.user_id == user_id)
        .order_by(GithubAccount.created_at)
    ))
    for acc in accs:
        token = await github_service.get_token(str(acc.id))
        if token:
            return {"Authorization": f"Bearer {token}"}, True
    return {}, False


OFFICIAL_REPO = "Victor-Alves0/AI-Workspace"


def _updater_token() -> str | None:
    """Token que o container `updater` gera ao subir (volume updater_state, só leitura
    aqui). Sem o arquivo = não há updater nesta instalação (desktop, dev)."""
    try:
        return Path(get_settings().updater_token_file).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


async def _updater(method: str, path: str) -> tuple[int, dict]:
    token = _updater_token()
    if not token:
        return 0, {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.request(method, get_settings().updater_url.rstrip("/") + path,
                                     headers={"Authorization": f"Bearer {token}"})
        return r.status_code, (r.json() if r.content else {})
    except (httpx.HTTPError, ValueError):
        return 0, {}


async def _update_status() -> dict | None:
    code, body = await _updater("GET", "/status")
    return body if code == 200 else None


@router.post("/update")
async def request_update(admin: User = Depends(require_admin)):
    """Pede ao container `updater` (o único com o socket do Docker) que puxe o último
    commit, reconstrua, suba e migre. Este processo nunca toca no Docker."""
    code, body = await _updater("POST", "/update")
    if code == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Atualizador indisponível — o serviço `updater` não está rodando nesta instalação.",
        )
    if code == 409:
        raise HTTPException(status.HTTP_409_CONFLICT, "Já há uma atualização em andamento")
    if code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"O atualizador recusou o pedido ({code}).")
    await audit_service.record("update_requested", user_id=admin.id)
    return {"ok": True}


@router.get("/update-check")
async def update_check(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    """Compara a versão local com o GitHub (release mais recente + último commit).
    Usa a conta GitHub conectada do admin quando houver — sem ela, repositório privado
    responde 404 e não há como checar."""
    cfg = await network_config.load_config()
    # normaliza na LEITURA também: um valor salvo antes (URL crua) se cura sozinho
    # repositório oficial do projeto, salvo override explícito (fork) na config
    repo = _normalize_repo(cfg.get("repo") or "") or OFFICIAL_REPO
    branch = (cfg.get("branch") or "main").strip()
    out: dict = {
        "current_version": __version__,
        "current_commit": (get_settings().git_commit or "").strip() or None,
        "repo": repo,
        "branch": branch,
        "latest_release": None,
        "latest_commit": None,
        "update_available": False,
        "commits_behind": None,   # True/False; None = imagem sem GIT_COMMIT (desconhecido)
        "authenticated": False,
        "error": None,
        # atualização pelo painel: o updater está de pé? e como foi o último pedido?
        "agent": _updater_token() is not None,
        "update_status": await _update_status(),
    }
    try:
        auth, has_token = await _github_auth_header(db, admin.id)
        out["authenticated"] = has_token
        async with httpx.AsyncClient(
            timeout=10, headers={"Accept": "application/vnd.github+json", **auth}
        ) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
            if r.status_code == 200:
                tag = (r.json() or {}).get("tag_name")
                out["latest_release"] = tag
                if tag:
                    out["update_available"] = tag.lstrip("v") != __version__.lstrip("v")
            c = await client.get(f"https://api.github.com/repos/{repo}/commits/{branch}")
            if c.status_code == 200:
                sha = (c.json() or {}).get("sha") or ""
                out["latest_commit"] = sha[:8]
                # o update.sh puxa o BRANCH: se a imagem foi construída de um commit
                # diferente do topo do branch, HÁ atualização — mesmo que a release
                # bata. Sem GIT_COMMIT na imagem, `commits_behind` fica None e só a
                # release manda (comportamento antigo, sem afirmar o que não se sabe).
                same = _same_commit(out["current_commit"], sha)
                if same is not None:
                    out["commits_behind"] = not same
                    if not same:
                        out["update_available"] = True
            elif c.status_code in (401, 403):
                out["error"] = ("O GitHub recusou o token da conta conectada "
                                f"(HTTP {c.status_code}) — reconecte em Integrações → GitHub.")
            elif c.status_code == 404 and out["latest_release"] is None:
                # 404 é ambíguo de propósito no GitHub: repo inexistente OU privado sem
                # acesso. Diferenciar aqui evita mandar o admin caçar um erro de digitação
                # que não existe (foi o caso deste projeto: repo privado, sem token).
                out["error"] = (
                    f"Repositório '{repo}' não encontrado — confira o owner/repo e o branch "
                    f"'{branch}'."
                    if has_token else
                    f"Não consegui ver '{repo}'. Se ele for PRIVADO, conecte sua conta em "
                    "Integrações → GitHub (o check passa a usá-la); se for público, confira "
                    "o owner/repo."
                )
    except httpx.HTTPError as exc:
        out["error"] = f"Falha ao consultar o GitHub: {exc}"
    return out


# --------------------------------------------------------------------------- #
# Backup completo / migração de sistema (pg_dump / pg_restore)
#
# Quase tudo vive no Postgres (usuários, chats, segredos cifrados, imagens
# geradas, vetores do mem0). A exceção são os ARQUIVOS do Codespace, que vivem
# em disco (volume codespace_data) — por isso o backup é um BUNDLE: pg_dump +
# a árvore do Codespace, num tar cifrado (AIWBK2). Para restaurar em outra
# máquina, o .env precisa do MESMO APP_SECRET (os segredos são cifrados com
# chave derivada dele, e o bundle inteiro também).
# --------------------------------------------------------------------------- #
def _pg_url() -> str:
    return get_settings().sync_database_url



def _require_pg_tools() -> None:
    if not all(shutil.which(b) for b in ("pg_dump", "pg_restore", "psql")):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "pg_dump/pg_restore/psql indisponíveis — reconstrua a imagem do server "
            "(docker compose build server) para habilitar o backup.",
        )


def _app_root() -> Path:
    """Raiz do app no container (/app) — onde vivem alembic.ini e alembic/."""
    return Path(__file__).resolve().parents[1]


def _known_revisions() -> set[str]:
    """Todas as revisões de migração que ESTA instalação conhece."""
    try:
        from alembic.script import ScriptDirectory
        script = ScriptDirectory(str(_app_root() / "alembic"))
        return {s.revision for s in script.walk_revisions()}
    except Exception:  # noqa: BLE001 - checagem é proteção, não pode bloquear sozinha
        logger.exception("não consegui listar as revisões do alembic")
        return set()


def _codespace_root() -> Path:
    return Path(get_settings().codespace_data_dir)


def _uploads_root() -> Path:
    return Path(get_settings().uploads_dir)


def _build_backup_bundle(dump_path: str, tar_path: str, data_key: str | None = None) -> None:
    """Empacota o dump do banco + os arquivos que vivem em disco num tar (bloqueante —
    roda em threadpool): `database.dump` na raiz, o Codespace sob `codespace/` e os
    anexos do chat sob `uploads/` (sem eles as mensagens restauradas apontariam para
    arquivos inexistentes). `data_key` (só no backup com senha) vai em `keys.json`:
    é a chave de DADOS da origem, que a restauração usa para recifrar os segredos."""
    import json as _json

    cs, up = _codespace_root(), _uploads_root()
    with tarfile.open(tar_path, "w") as tf:
        tf.add(dump_path, arcname="database.dump")
        if cs.is_dir():
            tf.add(str(cs), arcname="codespace")
        if up.is_dir():
            tf.add(str(up), arcname="uploads")
        if data_key:
            dados = _json.dumps({"data_key": data_key, "v": 1}).encode()
            info = tarfile.TarInfo("keys.json")
            info.size = len(dados)
            import io as _io
            tf.addfile(info, _io.BytesIO(dados))


def _bundle_data_key(tar_path: str) -> str | None:
    import json as _json

    with tarfile.open(tar_path) as tf:
        try:
            src = tf.extractfile("keys.json")
        except KeyError:
            return None
        if src is None:
            return None
        with src:
            return (_json.loads(src.read() or b"{}") or {}).get("data_key")


def _extract_db_dump(tar_path: str, dump_path: str) -> None:
    """Extrai só o `database.dump` de dentro do bundle para `dump_path`."""
    with tarfile.open(tar_path) as tf:
        src = tf.extractfile("database.dump")
        if src is None:
            raise RuntimeError("bundle de backup sem database.dump")
        with src, open(dump_path, "wb") as df:
            shutil.copyfileobj(src, df)


def _restore_codespace(tar_path: str) -> int:
    return _restore_tree(tar_path, "codespace/", _codespace_root())


def _restore_tree(tar_path: str, prefix: str, dest_root: Path) -> int:
    """Restaura a árvore `prefix` do bundle em `dest_root` (sobrescreve arquivo a
    arquivo). Ignora links e qualquer caminho que escape da raiz (proteção contra
    path traversal). Retorna nº de arquivos."""
    root = dest_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(tar_path) as tf:
        for m in tf.getmembers():
            if not m.name.startswith(prefix):
                continue
            rel = m.name[len(prefix):].lstrip("/")
            if not rel:
                continue
            dest = (root / rel).resolve()
            try:
                dest.relative_to(root)  # dentro da raiz?
            except ValueError:
                continue  # traversal — ignora
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                src = tf.extractfile(m)
                if src is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                # remove o destino antes de escrever: objetos do git são 0444
                # (read-only), então abrir em "wb" por cima daria PermissionError.
                # O unlink depende da permissão do DIRETÓRIO (temos), não do arquivo.
                if dest.exists() or dest.is_symlink():
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                with src, open(dest, "wb") as df:
                    shutil.copyfileobj(src, df)
                if m.mode:  # preserva o modo original (git deixa os objetos read-only)
                    try:
                        os.chmod(dest, m.mode)
                    except OSError:
                        pass
                n += 1
            # symlinks/hardlinks: ignorados por segurança
    return n


async def _dump_alembic_rev(path: str) -> str | None:
    """Revisão do alembic gravada DENTRO do dump (sem restaurar nada): extrai só a
    tabela alembic_version como SQL e lê o valor do COPY."""
    proc = await asyncio.create_subprocess_exec(
        # "-f -" = SQL no stdout (obrigatório no PG16+, que exige -d ou -f explícito)
        "pg_restore", "--data-only", "--table=alembic_version", "-f", "-", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    m = re.search(r"FROM stdin;\s*\n([^\s\\]+)", out.decode(errors="replace"))
    return m.group(1).strip() if m else None


class BackupIn(BaseModel):
    # senha do backup: permite restaurar em OUTRA instalação (outro APP_SECRET, como
    # o app desktop). Vazia = backup só para esta instalação (AIWBK2, pelo APP_SECRET).
    password: str = Field(default="", max_length=256)


@router.post("/backup")
async def export_backup_with_password(body: BackupIn, admin: User = Depends(require_admin)):
    password = body.password.strip()
    if password and len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A senha do backup precisa ter ao menos 8 caracteres.")
    return await export_backup(admin, password=password or None)


@router.get("/backup")
async def export_backup(admin: User = Depends(require_admin), password: str | None = None):
    """Baixa um backup completo do sistema: bundle cifrado com o dump do banco
    (pg_dump custom) + os arquivos que vivem em disco (Codespace, anexos). Com senha
    (AIWBK3) restaura em qualquer instalação; sem senha (AIWBK2) só onde o APP_SECRET
    é o mesmo. O pg_dump vai para um arquivo temporário, o bundle é montado num tar e
    então cifrado em streaming para o download."""
    _require_pg_tools()
    dump_f = tempfile.NamedTemporaryFile(suffix=".dump", delete=False)
    dump_path = dump_f.name
    dump_f.close()
    tar_f = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
    tar_path = tar_f.name
    tar_f.close()
    try:
        # pg_dump direto para arquivo (erros viram HTTP ANTES de começar o stream)
        proc = await asyncio.create_subprocess_exec(
            *db_restore.dump_argv(_pg_url(), dump_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            logger.error("pg_dump falhou: %s", err.decode(errors="replace")[-2000:])
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "pg_dump falhou ao gerar o backup.")
        # empacota dump + arquivos do Codespace (tarfile é bloqueante → threadpool)
        await run_in_threadpool(
            _build_backup_bundle, dump_path, tar_path, data_key_b64() if password else None)
    except BaseException:
        for p in (dump_path, tar_path):
            try:
                os.unlink(p)
            except OSError:
                pass
        raise

    async def stream():
        try:
            # cifra o bundle em repouso (AES-CTR): pela senha (AIWBK3, migrável) ou
            # pelo APP_SECRET (AIWBK2); header primeiro
            if password:
                header, enc = await run_in_threadpool(backup_password_encryptor, password)
            else:
                header, enc = backup_encryptor(BACKUP_MAGIC_V2)
            yield header
            with open(tar_path, "rb") as f:
                while chunk := f.read(256 * 1024):
                    yield enc.update(chunk)
            yield enc.finalize()
        finally:
            for p in (dump_path, tar_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    filename = f"aiworkspace-{datetime.now():%Y%m%d-%H%M}.backup"
    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore")
async def import_backup(
    file: UploadFile,
    password: str = Form(default=""),
    source_secret: str = Form(default=""),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Restaura um backup completo (SUBSTITUI todos os dados atuais).

    Fluxo: salva o upload em arquivo temporário → converte o dump em script SQL →
    derruba as outras conexões → aplica o script numa transação só (ver
    db_restore.py: falhou = nada muda). Depois, as sessões podem exigir novo login
    (os usuários passam a ser os do backup)."""
    _require_pg_tools()
    tmp = tempfile.NamedTemporaryFile(suffix=".backup", delete=False)
    dec_path: str | None = None    # payload decifrado (pg_dump OU tar do bundle)
    dump_path: str | None = None   # database.dump extraído do bundle (V2)
    bundle_path: str | None = None  # tar do bundle, p/ restaurar o Codespace depois
    work_dir = tempfile.mkdtemp(prefix="aiw-restore-")  # script SQL + prelúdio
    try:
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
        tmp.close()

        # Detecta o formato pelo header:
        #   AIWBK2 → bundle cifrado (pg_dump + arquivos do Codespace)
        #   AIWBK1 → pg_dump cifrado (formato anterior)
        #   nenhum → backup legado em texto claro (compat retroativa)
        src_path = tmp.name
        with open(tmp.name, "rb") as f:
            head = f.read(len(BACKUP_MAGIC))
        is_v1 = head == BACKUP_MAGIC
        is_v3 = head == BACKUP_MAGIC_V3
        is_v2 = head == BACKUP_MAGIC_V2 or is_v3  # V3 = mesmo bundle, cifrado por senha
        # chave de DADOS da origem, quando o backup vem de OUTRA instalação: os segredos
        # do banco (chaves de API, tokens) são recifrados para a chave local no fim
        source_key: str | None = None
        if is_v3 and not password.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este backup tem senha — informe a senha do backup.")
        if is_v1 or is_v2:
            dec = tempfile.NamedTemporaryFile(suffix=".dec", delete=False)
            dec_path = dec.name
            try:
                with open(tmp.name, "rb") as f:
                    if is_v3:
                        d = await run_in_threadpool(
                            backup_password_decryptor, f.read(BACKUP_V3_HEADER), password.strip())
                    else:
                        f.seek(len(head))
                        nonce = f.read(16)
                        d = backup_decryptor(nonce, source_secret.strip() or None)
                    while chunk := f.read(1024 * 1024):
                        dec.write(d.update(chunk))
                    dec.write(d.finalize())
                dec.close()
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha do backup incorreta.") from None
            except Exception:  # noqa: BLE001 — chave errada, arquivo corrompido…
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Não consegui decifrar o backup — use a senha do backup (ou, num backup "
                    "antigo sem senha, o APP_SECRET da instalação de origem).",
                ) from None
            if not is_v3 and source_secret.strip():
                from .crypto import _fernet_key_b64
                source_key = _fernet_key_b64(source_secret.strip())
            if is_v2:
                # bundle (tar): extrai o database.dump p/ restaurar; a árvore
                # codespace/ só é aplicada DEPOIS do pg_restore ter dado certo.
                bundle_path = dec_path
                dmp = tempfile.NamedTemporaryFile(suffix=".dump", delete=False)
                dump_path = dmp.name
                dmp.close()
                try:
                    await run_in_threadpool(_extract_db_dump, bundle_path, dump_path)
                    if is_v3:
                        source_key = await run_in_threadpool(_bundle_data_key, bundle_path)
                except Exception:  # noqa: BLE001 — chave errada vira tar ilegível
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Bundle de backup inválido (chave errada ou arquivo corrompido) — "
                        "nada foi alterado.",
                    ) from None
                src_path = dump_path
            else:
                src_path = dec_path

        # valida que é um dump do pg_dump (formato custom começa com "PGDMP")
        with open(src_path, "rb") as f:
            if f.read(5) != b"PGDMP":
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Arquivo inválido — envie um backup exportado por este painel (.backup).",
                )

        # backup de uma versão MAIS NOVA? A revisão do alembic no dump precisa ser
        # conhecida desta instalação — senão o restore "funciona" e o próximo boot
        # quebra no `alembic upgrade head` (Can't locate revision). Barra ANTES.
        dump_rev = await _dump_alembic_rev(src_path)
        known = _known_revisions()
        if dump_rev and known and dump_rev not in known:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Este backup vem de uma versão mais nova do sistema (migração "
                f"'{dump_rev}' é desconhecida desta instalação). Atualize este servidor "
                "(git pull + docker compose build) antes de restaurar.",
            )

        # passo 1 (sem tocar no banco): dump → script SQL. Dump corrompido para aqui.
        script_path = os.path.join(work_dir, "restore.sql")
        proc = await asyncio.create_subprocess_exec(
            *db_restore.script_argv(src_path, script_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("pg_restore (script) falhou: %s", stderr.decode(errors="replace")[-4000:])
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Backup ilegível (dump corrompido ou incompleto) — nada foi alterado.",
            )

        # encerra as demais conexões (pools do app) p/ liberar locks do restore
        await db.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ))
        await db.commit()

        # passo 2: esvazia o `public` + aplica o script, numa transação só
        proc = await asyncio.create_subprocess_exec(
            *db_restore.apply_argv(_pg_url(), db_restore.write_prelude(work_dir), script_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        err = stderr.decode(errors="replace")
        if proc.returncode != 0:
            logger.error("restore falhou: %s", err[-4000:])
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"restore falhou (código {proc.returncode}) — nada foi alterado, o "
                f"banco continua como estava: {err[-500:]}",
            )

        # backup de versão ANTIGA → traz o esquema ao presente já aqui (antes o app
        # rodava com esquema velho até alguém lembrar do upgrade e tudo quebrava)
        migrate_note = ""
        up = await asyncio.create_subprocess_exec(
            "alembic", "upgrade", "head", cwd=str(_app_root()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        up_out, _ = await up.communicate()
        if up.returncode != 0:
            logger.error("alembic upgrade pós-restore falhou: %s", up_out.decode(errors="replace")[-2000:])
            migrate_note = (
                " ATENÇÃO: as migrações pós-restore falharam — rode `alembic upgrade head` "
                "manualmente antes de usar o sistema."
            )

        # backup de OUTRA instalação: recifra os segredos (chaves de API, tokens OAuth,
        # campos cifrados, JSON) da chave de dados de origem para a desta instalação
        key_note = ""
        if source_key and source_key != data_key_b64():
            from cryptography.fernet import Fernet

            from .crypto import _fernet
            from .secret_rotation import rotate_keys
            try:
                res = await run_in_threadpool(
                    lambda: rotate_keys(Fernet(source_key), _fernet(), dry_run=False))
                logger.info("pós-restore: %d segredo(s) recifrado(s) para a chave local", res["changed"])
                if res["failed"]:
                    key_note = (f" ATENÇÃO: {res['failed']} segredo(s) não puderam ser recifrados "
                                "— reconecte essas integrações.")
            except Exception:  # noqa: BLE001 - o banco já foi restaurado; avisa
                logger.exception("pós-restore: falha ao recifrar os segredos")
                key_note = (" ATENÇÃO: os segredos (chaves de API, conexões) não foram "
                            "recifrados — reconecte as integrações.")

        # bundle: restaura os arquivos em disco (só depois do banco ok, p/ não
        # sobrescrever o disco se o pg_restore tivesse falhado). Best-effort.
        cs_note = ""
        if is_v2 and bundle_path:
            try:
                n = await run_in_threadpool(_restore_codespace, bundle_path)
                n_up = await run_in_threadpool(_restore_tree, bundle_path, "uploads/", _uploads_root())
                logger.info("pós-restore: %d arquivo(s) do Codespace e %d anexo(s) restaurados", n, n_up)
            except Exception:  # noqa: BLE001 - não pode bloquear o restore do banco
                logger.exception("pós-restore: falha ao restaurar arquivos do Codespace/anexos")
                cs_note = " ATENÇÃO: arquivos do Codespace/anexos não foram restaurados (veja os logs)."

        # a sessão do WhatsApp (Evolution) vive FORA deste backup — no banco
        # "evolution" (separado) e no volume evolution_instances (arquivos do
        # Baileys). Ao restaurar em outra máquina, a conexão volta alegando "open"
        # mas o pareamento não existe: a UI mostraria "conectado" mentindo. Zera o
        # status p/ ela aparecer como desconectada e pedir novo QR. (Cloud API
        # oficial não depende de sessão local, então só mexemos no Evolution.)
        try:
            await db.execute(text(
                "UPDATE whatsapp_connections "
                "SET state = jsonb_set(coalesce(state, '{}'::jsonb), '{status}', '\"close\"') "
                "WHERE provider = 'evolution'"
            ))
            await db.commit()
        except Exception:  # noqa: BLE001 - não pode bloquear o restore
            logger.exception("pós-restore: falha ao zerar status do WhatsApp")

        # caches em memória ficam órfãos do banco antigo → limpa (best-effort)
        try:
            from .db import engine
            from .tools import sift_service
            sift_service._cache.clear()
            await engine.dispose()
        except Exception:  # noqa: BLE001 - o restart recomendado resolve o resto
            logger.exception("limpeza pós-restore falhou (siga com o restart)")

        logger.warning("Backup restaurado pelo admin %s", admin.email)
        await audit_service.record("backup_restored", user_id=admin.id, detail={"migrate_note": bool(migrate_note)})
        return {
            "ok": True,
            "note": "Backup restaurado e migrações aplicadas. Se os usuários mudaram, faça "
                    "login novamente. Recomendado: reiniciar o server (docker compose "
                    "restart server)." + migrate_note + key_note + cs_note,
        }
    finally:
        # dec_path == bundle_path no V2 (o mesmo tar), então não repete
        for p in (tmp.name, dec_path, dump_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        shutil.rmtree(work_dir, ignore_errors=True)
