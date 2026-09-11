"""Ingestão de documentos para a Base de Conhecimento.

Extrai texto (pdf/docx/txt/md/csv/json), quebra em chunks com sobreposição, gera
embeddings locais (FastEmbed) e grava em `knowledge_chunks`. Rodado em background
(via `asyncio.create_task` na rota) usando um engine efêmero NullPool (padrão do
`automation/creator`) — as partes pesadas (extração/embedding, CPU-bound) vão para
o threadpool p/ não travar o event loop.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..models import KnowledgeDoc
from . import embeddings

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 1000
_OVERLAP_CHARS = 150
_MAX_CHUNKS = 2000  # teto de segurança por documento

# mantém referência das tasks de indexação em voo (evita GC prematuro)
_TASKS: set[asyncio.Task] = set()


def spawn_index(doc_id: uuid.UUID) -> None:
    """Agenda a indexação de um doc em background (fire-and-forget seguro)."""
    t = asyncio.create_task(index_doc(doc_id))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def resume_pending() -> int:
    """Retoma no boot os docs que ficaram no meio do caminho ("pending"/"indexing").

    A indexação é uma task em memória: se o servidor reinicia com ela em voo, o doc
    ficava PRESO em "Indexando" para sempre (visto ao vivo com um lote de imagens).
    Chamado no lifespan. Retorna quantos foram reagendados."""
    from sqlalchemy import select

    from ..db import SessionLocal

    async with SessionLocal() as db:
        rows = list(await db.scalars(
            select(KnowledgeDoc.id).where(KnowledgeDoc.status.in_(("pending", "indexing")))
        ))
    for did in rows:
        spawn_index(did)
    if rows:
        logger.info("knowledge: %d doc(s) de indexação retomados no boot", len(rows))
    return len(rows)


# --------------------------------------------------------------------------- #
# Extração de texto
# --------------------------------------------------------------------------- #
def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs)


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".svg")
_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".ogv", ".mkv")


def is_image(filename: str, mime: str) -> bool:
    return (mime or "").lower().startswith("image/") or (filename or "").lower().endswith(_IMAGE_EXTS)


def is_video(filename: str, mime: str) -> bool:
    return (mime or "").lower().startswith("video/") or (filename or "").lower().endswith(_VIDEO_EXTS)


def _media_text(filename: str, label: str) -> str:
    """Texto indexável de uma mídia (imagem/vídeo): o nome do arquivo vira descrição
    ("akeno-himejima-dxd-27.webp" → "akeno himejima dxd"). Título/tags entram pelo
    `_meta_prefix`, como nos demais docs. Os BYTES nunca são decodificados — era isso
    que gerava megabytes de lixo binário e travava a indexação."""
    stem = re.sub(r"\.[a-z0-9]+$", "", (filename or "").strip(), flags=re.I)
    # Números fazem parte da IDENTIDADE de coleções (`clip 1`, `clip 2`, `clip 3`).
    # Removê-los fazia todas essas mídias receberem exatamente o mesmo embedding e,
    # em empates determinísticos, a busca devolvia sempre o mesmo arquivo.
    words = re.sub(r"[-_.+%#]+", " ", stem).split()
    return f"{label}: " + (" ".join(words) if words else (filename or label.lower()))


def _image_text(filename: str) -> str:
    return _media_text(filename, "Imagem")


def _video_text(filename: str) -> str:
    return _media_text(filename, "Vídeo")


def extract_text(filename: str, mime: str, data: bytes) -> str:
    """Texto cru de um arquivo. Decide pelo mime e/ou extensão. Levanta em erro."""
    name = (filename or "").lower()
    mime = (mime or "").lower()
    if is_image(filename, mime):
        return _image_text(filename)
    if is_video(filename, mime):
        return _video_text(filename)
    if "pdf" in mime or name.endswith(".pdf"):
        return _extract_pdf(data)
    if "wordprocessingml" in mime or name.endswith(".docx"):
        return _extract_docx(data)
    # texto puro (txt/md/csv/json/…): decodifica tolerante
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #
def chunk_text(text: str) -> list[str]:
    """Quebra o texto em pedaços de ~`_CHUNK_CHARS` com ~`_OVERLAP_CHARS` de
    sobreposição, respeitando limites de parágrafo/sentença quando possível."""
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return []
    # unidades naturais (parágrafos); parágrafos gigantes caem no fatiamento por char
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > _CHUNK_CHARS:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_slice(p))
            continue
        if buf and len(buf) + len(p) + 2 > _CHUNK_CHARS:
            chunks.append(buf)
            buf = _tail(buf) + "\n\n" + p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks[:_MAX_CHUNKS] if c.strip()]


def _slice(s: str) -> list[str]:
    """Fatia um bloco grande por caracteres, com sobreposição."""
    out: list[str] = []
    step = _CHUNK_CHARS - _OVERLAP_CHARS
    for i in range(0, len(s), step):
        out.append(s[i : i + _CHUNK_CHARS])
        if i + _CHUNK_CHARS >= len(s):
            break
    return out


def _tail(s: str) -> str:
    """Últimos ~`_OVERLAP_CHARS` chars de `s` (p/ sobreposição entre chunks)."""
    return s[-_OVERLAP_CHARS:] if len(s) > _OVERLAP_CHARS else s


def _meta_prefix(meta: dict | None) -> str:
    """Linha curta com título/tags do doc (metadados), p/ prefixar cada chunk.
    Vazio quando não há metadados."""
    if not meta:
        return ""
    parts: list[str] = []
    title = (meta.get("title") or "").strip()
    if title:
        parts.append(title)
    tags = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()]
    if tags:
        parts.append("tags: " + ", ".join(tags))
    return ("[" + " · ".join(parts) + "]\n") if parts else ""


# --------------------------------------------------------------------------- #
# Indexação (background)
# --------------------------------------------------------------------------- #
_INSERT = sql_text(
    "INSERT INTO knowledge_chunks "
    "(id, doc_id, base_id, user_id, ordinal, text, embedding, created_at, updated_at) "
    "VALUES (:id, :doc_id, :base_id, :user_id, :ordinal, :text, CAST(:emb AS vector), now(), now())"
)


async def index_doc(doc_id: uuid.UUID) -> None:
    """Indexa um documento: extrai → chunk → embed → grava chunks. Idempotente
    (apaga chunks antigos do doc antes de inserir). Erros marcam `status=error`."""
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as db:
            doc = await db.get(KnowledgeDoc, doc_id)
            if doc is None or not doc.data:
                if doc is not None:
                    doc.status = "error"
                    doc.error = "arquivo vazio"
                    await db.commit()
                return
            doc.status = "indexing"
            doc.error = None
            await db.commit()
            filename, mime, base_id, user_id = doc.filename, doc.mime, doc.base_id, doc.user_id
            meta_prefix = _meta_prefix(doc.meta)
            raw = bytes(doc.data)

        try:
            text = await run_in_threadpool(extract_text, filename, mime, raw)
            chunks = chunk_text(text)
            if not chunks:
                raise ValueError("nenhum texto extraído do arquivo")
            # metadados (título/tags) viram prefixo de cada chunk: melhoram tanto o
            # embedding (recuperação) quanto o trecho citado ao usuário.
            if meta_prefix:
                chunks = [meta_prefix + c for c in chunks]
            vecs = await run_in_threadpool(embeddings.embed_texts, chunks)
        except Exception as exc:  # noqa: BLE001
            logger.warning("index_doc extração/embedding falhou (%s): %s", doc_id, exc)
            async with Session() as db:
                d = await db.get(KnowledgeDoc, doc_id)
                if d is not None:
                    d.status = "error"
                    d.error = str(exc)[:500]
                    d.chunk_count = 0
                    await db.commit()
            return

        async with Session() as db:
            await db.execute(sql_text("DELETE FROM knowledge_chunks WHERE doc_id = :d"), {"d": doc_id})
            for i, (chunk, vec) in enumerate(zip(chunks, vecs)):
                await db.execute(
                    _INSERT,
                    {
                        "id": uuid.uuid4(),
                        "doc_id": doc_id,
                        "base_id": base_id,
                        "user_id": user_id,
                        "ordinal": i,
                        "text": chunk,
                        "emb": embeddings.to_pgvector(vec),
                    },
                )
            d = await db.get(KnowledgeDoc, doc_id)
            if d is not None:
                d.status = "ready"
                d.error = None
                d.chunk_count = len(chunks)
            await db.commit()
        logger.info("index_doc ok (%s): %d chunks", doc_id, len(chunks))
    finally:
        await eng.dispose()
