"""Base de Conhecimento (RAG): CRUD de bases, upload/indexação de documentos e
download assinado do original.

Espelha `memory_routes` (bancos) + o upload do `admin_routes`. A indexação roda em
background (`asyncio.create_task(ingest.index_doc)`), então o upload responde na hora
e o status do doc caminha pending → indexing → ready/error.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .knowledge import enrich, ingest
from .knowledge.links import sign_doc_url, verify_doc_token
from .models import (
    Chat,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDoc,
    KnowledgeEnrichment,
    KnowledgeFolder,
    ModelConfig,
    User,
)

logger = logging.getLogger(__name__)
# refs dos jobs de enriquecimento em background (o create_task só guarda ref fraca)
_enrich_tasks: set[asyncio.Task] = set()

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_MAX_BYTES = 25 * 1024 * 1024  # 25 MB por arquivo (documentos/imagens)
_MAX_VIDEO_BYTES = 200 * 1024 * 1024  # vídeos são maiores (guardados como bytea)
_KINDS = ("kb", "brain")  # kb = RAG de documentos | brain = cérebro de notas

_spawn_index = ingest.spawn_index


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class BaseIn(BaseModel):
    name: str
    description: str = ""
    tags: list[str] | None = None
    # imutável após a criação (um cérebro não vira base RAG nem vice-versa)
    kind: str = "kb"


class BaseUpdate(BaseModel):
    # tudo opcional: um PATCH só-nome não pode zerar a descrição/etiquetas
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class BaseOut(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    kind: str = "kb"
    doc_count: int = 0
    chunk_count: int = 0


class DocOut(BaseModel):
    id: str
    base_id: str
    folder_id: str | None = None
    filename: str
    mime: str
    size: int
    status: str
    error: str | None = None
    chunk_count: int
    meta: dict | None = None
    created_at: str | None = None


class FolderIn(BaseModel):
    name: str
    parent_id: str | None = None


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    # sentinela p/ mover à raiz: `to_root=True` zera o parent_id
    to_root: bool = False


class FolderOut(BaseModel):
    id: str
    base_id: str
    name: str
    parent_id: str | None = None


class TextDocIn(BaseModel):
    filename: str
    content: str = ""
    folder_id: str | None = None


class TextContentIn(BaseModel):
    content: str = ""


class DocPatch(BaseModel):
    folder_id: str | None = None
    to_root: bool = False
    meta: dict | None = None


def _is_text_doc(d: KnowledgeDoc) -> bool:
    """Doc editável como texto: mime text/* ou extensão de texto conhecida."""
    name = (d.filename or "").lower()
    return (d.mime or "").startswith("text/") or name.endswith(
        (".txt", ".md", ".markdown", ".csv", ".json", ".log", ".yaml", ".yml")
    )


def _doc_out(d: KnowledgeDoc) -> DocOut:
    return DocOut(
        id=str(d.id), base_id=str(d.base_id),
        folder_id=str(d.folder_id) if d.folder_id else None,
        filename=d.filename, mime=d.mime,
        size=d.size, status=d.status, error=d.error, chunk_count=d.chunk_count,
        meta=d.meta or None,
        created_at=d.created_at.isoformat() if d.created_at else None,
    )


def _folder_out(f: KnowledgeFolder) -> FolderOut:
    return FolderOut(
        id=str(f.id), base_id=str(f.base_id), name=f.name,
        parent_id=str(f.parent_id) if f.parent_id else None,
    )


async def _owned_base(db: AsyncSession, user: User, base_id: uuid.UUID) -> KnowledgeBase:
    b = await db.get(KnowledgeBase, base_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Base não encontrada")
    return b


async def _owned_doc(db: AsyncSession, user: User, doc_id: uuid.UUID) -> KnowledgeDoc:
    d = await db.get(KnowledgeDoc, doc_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    return d


async def _owned_folder(db: AsyncSession, user: User, folder_id: uuid.UUID) -> KnowledgeFolder:
    f = await db.get(KnowledgeFolder, folder_id)
    if f is None or f.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pasta não encontrada")
    return f


def _clean_tags(raw) -> list[str]:
    out: list[str] = []
    for t in raw or []:
        s = str(t).strip()[:40]
        if s and s not in out:
            out.append(s)
    return out[:20]


# --------------------------------------------------------------------------- #
# Bases
# --------------------------------------------------------------------------- #
@router.get("/bases", response_model=list[BaseOut])
async def list_bases(
    kind: str = "kb",
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    if kind not in _KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tipo inválido")
    bases = list(await db.scalars(
        select(KnowledgeBase).where(
            KnowledgeBase.user_id == user.id, KnowledgeBase.kind == kind
        ).order_by(KnowledgeBase.created_at)
    ))
    # contagens (docs + soma de chunks) por base, numa query
    rows = (await db.execute(
        select(
            KnowledgeDoc.base_id,
            func.count(KnowledgeDoc.id),
            func.coalesce(func.sum(KnowledgeDoc.chunk_count), 0),
        ).where(KnowledgeDoc.user_id == user.id).group_by(KnowledgeDoc.base_id)
    )).all()
    counts = {r[0]: (r[1], r[2]) for r in rows}
    return [
        BaseOut(
            id=str(b.id), name=b.name, description=b.description or "", tags=b.tags or [],
            kind=b.kind or "kb",
            doc_count=counts.get(b.id, (0, 0))[0], chunk_count=counts.get(b.id, (0, 0))[1],
        )
        for b in bases
    ]


@router.post("/bases", response_model=BaseOut)
async def create_base(body: BaseIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    if body.kind not in _KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tipo inválido")
    b = KnowledgeBase(
        user_id=user.id, name=(body.name or "Base").strip()[:120],
        description=(body.description or "").strip(),
        tags=_clean_tags(body.tags), kind=body.kind,
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return BaseOut(
        id=str(b.id), name=b.name, description=b.description or "",
        tags=b.tags or [], kind=b.kind,
    )


@router.patch("/bases/{base_id}", response_model=BaseOut)
async def update_base(
    base_id: uuid.UUID, body: BaseUpdate,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    b = await _owned_base(db, user, base_id)
    if body.name is not None:
        b.name = (body.name or "Base").strip()[:120]
    if body.description is not None:
        b.description = body.description.strip()
    if body.tags is not None:
        b.tags = _clean_tags(body.tags)
    # `kind` é imutável: ignorado no PATCH
    await db.commit()
    return BaseOut(
        id=str(b.id), name=b.name, description=b.description or "",
        tags=b.tags or [], kind=b.kind or "kb",
    )


@router.delete("/bases/{base_id}")
async def delete_base(base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    b = await _owned_base(db, user, base_id)
    await db.delete(b)  # cascade apaga docs + chunks
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Documentos
# --------------------------------------------------------------------------- #
@router.get("/bases/{base_id}/docs", response_model=list[DocOut])
async def list_docs(base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await _owned_base(db, user, base_id)
    docs = list(await db.scalars(
        select(KnowledgeDoc).where(KnowledgeDoc.base_id == base_id).order_by(KnowledgeDoc.created_at)
    ))
    return [_doc_out(d) for d in docs]


@router.post("/bases/{base_id}/docs", response_model=list[DocOut])
async def upload_docs(
    base_id: uuid.UUID, files: list[UploadFile], folder_id: str | None = None,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _owned_base(db, user, base_id)
    fid = None
    if folder_id:
        folder = await _owned_folder(db, user, uuid.UUID(folder_id))
        if folder.base_id != base_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pasta de outra base")
        fid = folder.id
    created: list[KnowledgeDoc] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        is_vid = ingest.is_video(f.filename or "", f.content_type or "")
        cap = _MAX_VIDEO_BYTES if is_vid else _MAX_BYTES
        if len(data) > cap:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{f.filename}: arquivo acima de {cap // (1024 * 1024)} MB",
            )
        d = KnowledgeDoc(
            base_id=base_id, user_id=user.id, folder_id=fid,
            filename=(f.filename or "documento")[:255],
            mime=(f.content_type or "")[:128], size=len(data),
            status="pending", chunk_count=0, data=data,
        )
        db.add(d)
        created.append(d)
    await db.commit()
    for d in created:
        await db.refresh(d)
        _spawn_index(d.id)
    return [_doc_out(d) for d in created]


# --------------------------------------------------------------------------- #
# Pastas (explorador)
# --------------------------------------------------------------------------- #
@router.get("/bases/{base_id}/folders", response_model=list[FolderOut])
async def list_folders(base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    await _owned_base(db, user, base_id)
    folders = list(await db.scalars(
        select(KnowledgeFolder).where(KnowledgeFolder.base_id == base_id).order_by(KnowledgeFolder.name)
    ))
    return [_folder_out(f) for f in folders]


@router.post("/bases/{base_id}/folders", response_model=FolderOut)
async def create_folder(
    base_id: uuid.UUID, body: FolderIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _owned_base(db, user, base_id)
    parent_id = None
    if body.parent_id:
        parent = await _owned_folder(db, user, uuid.UUID(body.parent_id))
        if parent.base_id != base_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pasta-pai de outra base")
        parent_id = parent.id
    f = KnowledgeFolder(
        base_id=base_id, user_id=user.id,
        name=(body.name or "Pasta").strip()[:160], parent_id=parent_id,
    )
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _folder_out(f)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: uuid.UUID, body: FolderPatch,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    f = await _owned_folder(db, user, folder_id)
    if body.name is not None:
        f.name = (body.name or "Pasta").strip()[:160]
    if body.to_root:
        f.parent_id = None
    elif body.parent_id is not None:
        if body.parent_id == str(folder_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pasta não pode ser pai de si mesma")
        parent = await _owned_folder(db, user, uuid.UUID(body.parent_id))
        if parent.base_id != f.base_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pasta-pai de outra base")
        # impede ciclo: o novo pai não pode ser descendente de `f` (subiria em anel)
        cur: KnowledgeFolder | None = parent
        seen = 0
        while cur is not None and seen < 100:
            if cur.id == folder_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Não é possível mover uma pasta para dentro dela mesma")
            cur = await db.get(KnowledgeFolder, cur.parent_id) if cur.parent_id else None
            seen += 1
        f.parent_id = parent.id
    await db.commit()
    return _folder_out(f)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    f = await _owned_folder(db, user, folder_id)
    # docs e subpastas sobem para o pai (não some documento junto com a pasta)
    await db.execute(
        update(KnowledgeDoc).where(KnowledgeDoc.folder_id == folder_id).values(folder_id=f.parent_id)
    )
    await db.execute(
        update(KnowledgeFolder).where(KnowledgeFolder.parent_id == folder_id).values(parent_id=f.parent_id)
    )
    await db.delete(f)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Documento de texto (criar/editar no próprio explorador)
# --------------------------------------------------------------------------- #
@router.post("/bases/{base_id}/docs/text", response_model=DocOut)
async def create_text_doc(
    base_id: uuid.UUID, body: TextDocIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    base = await _owned_base(db, user, base_id)
    folder_id = None
    if body.folder_id:
        folder = await _owned_folder(db, user, uuid.UUID(body.folder_id))
        if folder.base_id != base_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pasta de outra base")
        folder_id = folder.id
    name = (body.filename or "documento").strip()[:255]
    if "." not in name:
        # num cérebro a criação inline é uma NOTA markdown; em base RAG, texto puro
        name += ".md" if base.kind == "brain" else ".txt"
    data = (body.content or "").encode("utf-8")
    d = KnowledgeDoc(
        base_id=base_id, user_id=user.id, folder_id=folder_id,
        filename=name,
        mime="text/markdown" if name.lower().endswith((".md", ".markdown")) else "text/plain",
        size=len(data),
        status="pending", chunk_count=0, data=data,
    )
    db.add(d)
    await db.commit()
    await db.refresh(d)
    _spawn_index(d.id)
    return _doc_out(d)


@router.get("/docs/{doc_id}/text")
async def get_doc_text(doc_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    d = await _owned_doc(db, user, doc_id)
    if not _is_text_doc(d):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este documento não é editável como texto")
    content = bytes(d.data or b"").decode("utf-8", errors="replace")
    return {"id": str(d.id), "filename": d.filename, "content": content}


@router.put("/docs/{doc_id}/text", response_model=DocOut)
async def update_doc_text(
    doc_id: uuid.UUID, body: TextContentIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    d = await _owned_doc(db, user, doc_id)
    if not _is_text_doc(d):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este documento não é editável como texto")
    data = (body.content or "").encode("utf-8")
    d.data = data
    d.size = len(data)
    d.status = "pending"
    d.error = None
    await db.commit()
    await db.refresh(d)
    _spawn_index(d.id)
    return _doc_out(d)


@router.patch("/docs/{doc_id}", response_model=DocOut)
async def update_doc(
    doc_id: uuid.UUID, body: DocPatch,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Move um doc de pasta e/ou edita seus metadados (título/descrição/tags).
    Mudar metadados dispara reindexação (o prefixo entra em cada chunk)."""
    d = await _owned_doc(db, user, doc_id)
    if body.to_root:
        d.folder_id = None
    elif body.folder_id is not None:
        folder = await _owned_folder(db, user, uuid.UUID(body.folder_id))
        if folder.base_id != d.base_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pasta de outra base")
        d.folder_id = folder.id
    reindex = False
    if body.meta is not None:
        d.meta = {
            "title": str(body.meta.get("title") or "").strip()[:200],
            "description": str(body.meta.get("description") or "").strip()[:1000],
            "tags": _clean_tags(body.meta.get("tags")),
        }
        reindex = True
    await db.commit()
    await db.refresh(d)
    if reindex and d.data:
        d.status = "pending"
        await db.commit()
        _spawn_index(d.id)
    return _doc_out(d)


# --------------------------------------------------------------------------- #
# Referências p/ o compositor ("#"): bases ACESSÍVEIS ao modelo/chat + árvore
# --------------------------------------------------------------------------- #
@router.get("/refs")
async def list_refs(
    chat_id: str | None = None, model_config_id: str | None = None,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Bases que o modelo/chat pode consultar (mesmo gate do RAG) com pastas + docs
    prontos, p/ o menu "#" do compositor. Só o que está ACOPLADO aparece."""
    from .chat.turn_setup import _resolve_knowledge  # lazy: evita ciclo de import

    chat = None
    if chat_id:
        chat = await db.get(Chat, uuid.UUID(chat_id))
        if chat is not None and chat.user_id != user.id:
            chat = None
    mc = None
    mcid = model_config_id or (str(chat.model_config_id) if chat and chat.model_config_id else None)
    if mcid:
        mc = await db.get(ModelConfig, uuid.UUID(mcid))
        if mc is not None and mc.user_id != user.id:
            mc = None
    kn = _resolve_knowledge(chat, mc, user)
    base_ids = [uuid.UUID(b) for b in kn.get("bases") or []]
    if not base_ids:
        return []
    bases = list(await db.scalars(
        select(KnowledgeBase).where(
            KnowledgeBase.id.in_(base_ids), KnowledgeBase.user_id == user.id,
            # defesa: cérebros nunca aparecem no menu "#" (têm acoplamento próprio)
            KnowledgeBase.kind == "kb",
        )
    ))
    folders = list(await db.scalars(
        select(KnowledgeFolder).where(KnowledgeFolder.base_id.in_(base_ids))
    ))
    docs = list(await db.scalars(
        select(KnowledgeDoc).where(
            KnowledgeDoc.base_id.in_(base_ids), KnowledgeDoc.status == "ready"
        )
    ))
    fol_by_base: dict[uuid.UUID, list] = {}
    for f in folders:
        fol_by_base.setdefault(f.base_id, []).append(
            {"id": str(f.id), "name": f.name, "parent_id": str(f.parent_id) if f.parent_id else None}
        )
    doc_by_base: dict[uuid.UUID, list] = {}
    for d in docs:
        doc_by_base.setdefault(d.base_id, []).append(
            {"id": str(d.id), "filename": d.filename, "folder_id": str(d.folder_id) if d.folder_id else None}
        )
    return [
        {
            "id": str(b.id), "name": b.name,
            "folders": fol_by_base.get(b.id, []),
            "docs": doc_by_base.get(b.id, []),
        }
        for b in bases
    ]


@router.post("/docs/{doc_id}/reindex", response_model=DocOut)
async def reindex_doc(doc_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    d = await _owned_doc(db, user, doc_id)
    if not d.data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Documento sem bytes originais para reindexar")
    d.status = "pending"
    d.error = None
    await db.commit()
    await db.refresh(d)
    _spawn_index(d.id)
    return _doc_out(d)


@router.delete("/docs/{doc_id}")
async def delete_doc(doc_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    d = await _owned_doc(db, user, doc_id)
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id))
    await db.delete(d)
    await db.commit()
    return {"ok": True}


@router.get("/docs/{doc_id}/link")
async def get_doc_link(
    doc_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """URL-capacidade assinada p/ VISUALIZAR o arquivo original no explorador
    (imagem/PDF num `<img>`/`<iframe>`, que não mandam cookie cross-origin).
    Autoriza por sessão + posse; a URL em si dispensa cookie (mesmo mecanismo das
    imagens que a IA exibe no chat)."""
    d = await _owned_doc(db, user, doc_id)
    return {"url": sign_doc_url(str(d.id)), "mime": d.mime, "filename": d.filename}


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """'bytes=start-end' → (start, end) inclusivo, ou None se ausente/ inválido.
    Suporta sufixo ('bytes=-N' = últimos N) e fim aberto ('bytes=N-')."""
    if not header or not header.startswith("bytes=") or size <= 0:
        return None
    spec = header[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    a, b = spec.split("-", 1)
    try:
        if a == "":                       # bytes=-N (últimos N)
            n = int(b)
            return (max(0, size - n), size - 1) if n > 0 else None
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


def _content_disposition(filename: str) -> str:
    """Content-Disposition robusto a nomes com acento/unicode (ex.: "Galvão", CJK,
    emoji). Cabeçalhos HTTP são latin-1: um nome fora disso quebraria o download. Damos
    um fallback ASCII + o nome real via RFC 5987 (`filename*=UTF-8''…`)."""
    from urllib.parse import quote
    ascii_name = (filename.encode("ascii", "ignore").decode("ascii") or "documento").replace('"', "")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/docs/{doc_id}/raw")
async def get_doc_raw(
    doc_id: uuid.UUID, request: Request, t: str = "", db: AsyncSession = Depends(get_db)
):
    """Baixa o arquivo original de uma fonte citada. Autoriza pelo token assinado
    `t` (URL-capacidade), então funciona num link direto sem cookie. Honra o header
    `Range` (206) para que <video>/<audio> possam buscar (seek) no player."""
    if not verify_doc_token(str(doc_id), t):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")
    d = await db.get(KnowledgeDoc, doc_id)
    if d is None or not d.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    blob = bytes(d.data)
    mime = d.mime or "application/octet-stream"
    disp = _content_disposition(d.filename or "documento")
    rng = _parse_range(request.headers.get("range", ""), len(blob))
    if rng is not None:
        start, end = rng
        return Response(
            content=blob[start : end + 1],
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=mime,
            headers={
                "Content-Disposition": disp,
                "Content-Range": f"bytes {start}-{end}/{len(blob)}",
                "Accept-Ranges": "bytes",
            },
        )
    return Response(
        content=blob,
        media_type=mime,
        headers={"Content-Disposition": disp, "Accept-Ranges": "bytes"},
    )


# --------------------------------------------------------------------------- #
# Enriquecedor com IA: gera título/descrição/tags (proposta) → aprovar/descartar
# --------------------------------------------------------------------------- #
class EnrichIn(BaseModel):
    model_config = {"protected_namespaces": ()}
    doc_ids: list[str] | None = None   # itens específicos
    folder_id: str | None = None       # ou uma pasta inteira
    all: bool = False                  # ou a base inteira
    model: str = ""                    # id do modelo (OpenRouter/ollama/codex) que gera
    extra_prompt: str = ""             # instrução extra ("põe tag amarelo em quem tem carro")


class EnrichmentOut(BaseModel):
    id: str
    doc_id: str
    filename: str
    mime: str
    folder: str | None
    status: str
    title: str
    description: str
    tags: list
    error: str | None


def _enrichment_out(e: KnowledgeEnrichment, doc: KnowledgeDoc | None, folder_name: str | None) -> EnrichmentOut:
    return EnrichmentOut(
        id=str(e.id), doc_id=str(e.doc_id),
        filename=(doc.filename if doc else "") or "documento",
        mime=(doc.mime if doc else "") or "",
        folder=folder_name,
        status=e.status, title=e.title, description=e.description,
        tags=e.tags or [], error=e.error,
    )


@router.post("/bases/{base_id}/enrich")
async def enrich_docs(
    base_id: uuid.UUID, body: EnrichIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Enfileira o enriquecimento (proposta de tags/descrição/título) dos docs
    escolhidos — uma seleção, uma pasta ou a base toda — e roda em segundo plano.
    A UI acompanha por GET .../enrichments e aprova/descarta cada proposta."""
    await _owned_base(db, user, base_id)
    if not (body.model or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Escolha um modelo para gerar.")
    # resolve alvo: doc_ids explícitos | pasta | base inteira (só docs com conteúdo)
    q = select(KnowledgeDoc).where(
        KnowledgeDoc.base_id == base_id, KnowledgeDoc.user_id == user.id,
        KnowledgeDoc.data.isnot(None),
    )
    if body.doc_ids:
        ids = [uuid.UUID(x) for x in body.doc_ids]
        q = q.where(KnowledgeDoc.id.in_(ids))
    elif body.folder_id:
        q = q.where(KnowledgeDoc.folder_id == uuid.UUID(body.folder_id))
    docs = list(await db.scalars(q))
    if not docs:
        return {"queued": 0}
    # provedor (chave/base_url) a partir do id do modelo escolhido
    from .chat.turn_setup import _resolve_provider
    api_key, base_url = await _resolve_provider(db, user, body.model)
    # não reenfileira docs que já têm uma proposta pendente/pronta em aberto
    open_rows = await db.scalars(
        select(KnowledgeEnrichment.doc_id).where(
            KnowledgeEnrichment.base_id == base_id,
            KnowledgeEnrichment.status.in_(("pending", "ready")),
        )
    )
    open_docs = {str(x) for x in open_rows}
    enr_ids: list[uuid.UUID] = []
    for d in docs:
        if str(d.id) in open_docs:
            continue
        e = KnowledgeEnrichment(
            user_id=user.id, base_id=base_id, doc_id=d.id, status="pending",
            model=body.model, extra_prompt=(body.extra_prompt or "")[:2000],
        )
        db.add(e)
        await db.flush()
        enr_ids.append(e.id)
    await db.commit()
    if enr_ids:
        task = asyncio.create_task(enrich.run_job(enr_ids, body.model, api_key, base_url))
        _enrich_tasks.add(task)
        task.add_done_callback(_enrich_tasks.discard)
    return {"queued": len(enr_ids)}


@router.get("/bases/{base_id}/enrichments", response_model=list[EnrichmentOut])
async def list_enrichments(
    base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Propostas em aberto desta base (pending/gerando, ready/pronta, error)."""
    await _owned_base(db, user, base_id)
    rows = list(await db.scalars(
        select(KnowledgeEnrichment)
        .where(KnowledgeEnrichment.base_id == base_id)
        .order_by(KnowledgeEnrichment.created_at.asc())
    ))
    if not rows:
        return []
    docs = {d.id: d for d in await db.scalars(
        select(KnowledgeDoc).where(KnowledgeDoc.id.in_([r.doc_id for r in rows]))
    )}
    fol_ids = {d.folder_id for d in docs.values() if d.folder_id}
    folders = {f.id: f.name for f in await db.scalars(
        select(KnowledgeFolder).where(KnowledgeFolder.id.in_(fol_ids))
    )} if fol_ids else {}
    out = []
    for r in rows:
        doc = docs.get(r.doc_id)
        fname = folders.get(doc.folder_id) if (doc and doc.folder_id) else None
        out.append(_enrichment_out(r, doc, fname))
    return out


async def _owned_enrichment(db: AsyncSession, user: User, enr_id: uuid.UUID) -> KnowledgeEnrichment:
    e = await db.get(KnowledgeEnrichment, enr_id)
    if e is None or e.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposta não encontrada")
    return e


@router.post("/enrichments/{enr_id}/approve", response_model=DocOut)
async def approve_enrichment(
    enr_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Aplica a proposta ao doc (funde no meta) e reindexa; some da lista."""
    e = await _owned_enrichment(db, user, enr_id)
    if e.status != "ready":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposta ainda não está pronta")
    doc = await db.get(KnowledgeDoc, e.doc_id)
    if doc is None:
        await db.delete(e)
        await db.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não existe mais")
    await enrich.apply_to_doc(db, doc, e)
    await db.delete(e)
    await db.commit()
    await db.refresh(doc)
    if doc.data:
        _spawn_index(doc.id)
    return _doc_out(doc)


@router.delete("/enrichments/{enr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_enrichment(
    enr_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Descarta uma proposta (não altera o doc)."""
    e = await _owned_enrichment(db, user, enr_id)
    await db.delete(e)
    await db.commit()


@router.post("/bases/{base_id}/enrichments/approve-all")
async def approve_all_enrichments(
    base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Aprova todas as propostas PRONTAS da base de uma vez."""
    await _owned_base(db, user, base_id)
    rows = list(await db.scalars(
        select(KnowledgeEnrichment).where(
            KnowledgeEnrichment.base_id == base_id, KnowledgeEnrichment.status == "ready",
        )
    ))
    reindex: list[uuid.UUID] = []
    for e in rows:
        doc = await db.get(KnowledgeDoc, e.doc_id)
        if doc is not None:
            await enrich.apply_to_doc(db, doc, e)
            if doc.data:
                reindex.append(doc.id)
        await db.delete(e)
    await db.commit()  # persiste o meta ANTES de reindexar (index_doc lê em sessão própria)
    for did in reindex:
        _spawn_index(did)
    return {"approved": len(reindex)}


@router.delete("/bases/{base_id}/enrichments")
async def dismiss_all_enrichments(
    base_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    """Descarta TODAS as propostas em aberto da base (limpa a lista)."""
    await _owned_base(db, user, base_id)
    res = await db.execute(
        delete(KnowledgeEnrichment).where(KnowledgeEnrichment.base_id == base_id)
    )
    await db.commit()
    return {"dismissed": res.rowcount}
