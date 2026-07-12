"""Second brain: notas markdown autorais [[interligadas]] que a IA lê/escreve.

Um "cérebro" é uma `KnowledgeBase` com `kind="brain"` — reusa pastas/docs/chunks,
indexação (`ingest`) e recuperação (`retrieval`) da Base de Conhecimento. Este
módulo cobre o que é específico de notas: resolução de [[wikilinks]] (por nome do
arquivo, fallback no título dos metadados), o grafo de notas (nós + arestas + ghost
nodes, parseado on-demand — sem tabela de links a sincronizar) e a escrita/upsert
de notas pela IA (tool `brain`, action `write`).

Sessões próprias (`SessionLocal`) como em `retrieval.py`: funciona igual na request
e na geração em background.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select

from ..db import SessionLocal
from ..models import KnowledgeBase, KnowledgeDoc
from . import ingest

# [[Alvo]], [[Alvo|apelido]], [[Alvo#seção]] — captura só o alvo
WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|[^\[\]\n]*)?\]\]")

_NOTE_EXTS = (".md", ".markdown", ".txt")
# caracteres proibidos em nome de arquivo (Windows/Unix) — trocados por espaço
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|]+')


def note_key(name: str) -> str:
    """Chave canônica de uma nota p/ casar [[wikilinks]] com arquivos: sem extensão,
    minúscula, espaços/underscores colapsados."""
    s = (name or "").strip()
    low = s.lower()
    for ext in _NOTE_EXTS:
        if low.endswith(ext):
            s = s[: -len(ext)]
            break
    return re.sub(r"[\s_]+", " ", s.strip()).lower()


def parse_links(text: str) -> list[str]:
    """Alvos dos [[wikilinks]] no texto, na ordem, sem duplicatas (por chave)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in WIKILINK_RE.finditer(text or ""):
        target = m.group(1).strip()
        key = note_key(target)
        if target and key and key not in seen:
            seen.add(key)
            out.append(target)
    return out


def build_graph(docs: list[dict]) -> dict:
    """Grafo de notas a partir de `[{id, filename, title, text}]` (função pura).

    Nós = notas (+ "ghost nodes" p/ links sem alvo, id `ghost:<chave>`, estilo
    Obsidian); arestas = wikilinks resolvidos por `note_key` (self-links ignorados).
    """
    by_key: dict[str, dict] = {}
    for d in docs:
        k = note_key(d.get("filename") or "")
        tk = note_key(d.get("title") or "")
        if k:
            by_key.setdefault(k, d)
        if tk:
            by_key.setdefault(tk, d)

    nodes: dict[str, dict] = {}
    for d in docs:
        nodes[str(d["id"])] = {
            "id": str(d["id"]),
            "title": (d.get("title") or "").strip()
            or note_key(d.get("filename") or "") or "nota",
            "links_out": 0,
            "links_in": 0,
            "ghost": False,
        }

    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for d in docs:
        src = str(d["id"])
        for target in parse_links(d.get("text") or ""):
            key = note_key(target)
            dst_doc = by_key.get(key)
            dst = str(dst_doc["id"]) if dst_doc is not None else f"ghost:{key}"
            if dst == src or (src, dst) in seen_edges:
                continue
            seen_edges.add((src, dst))
            if dst.startswith("ghost:") and dst not in nodes:
                nodes[dst] = {
                    "id": dst, "title": target.strip(),
                    "links_out": 0, "links_in": 0, "ghost": True,
                }
            edges.append({"source": src, "target": dst})
            nodes[src]["links_out"] += 1
            nodes[dst]["links_in"] += 1
    return {"nodes": list(nodes.values()), "edges": edges}


# --------------------------------------------------------------------------- #
# Acesso a notas (sessão própria — funciona em request e em background)
# --------------------------------------------------------------------------- #
def _is_note(d: KnowledgeDoc) -> bool:
    name = (d.filename or "").lower()
    return (d.mime or "").startswith("text/") or name.endswith(_NOTE_EXTS)


def _doc_title(d: KnowledgeDoc) -> str:
    meta_title = ((d.meta or {}).get("title") or "").strip()
    if meta_title:
        return meta_title
    name = d.filename or "nota"
    low = name.lower()
    for ext in _NOTE_EXTS:
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def _uuids(ids: list) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for i in ids or []:
        try:
            out.append(i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)))
        except (ValueError, TypeError):
            continue
    return out


def _uid(user_id) -> uuid.UUID:
    """Normaliza o user_id p/ UUID — o orchestrator passa string; comparações em
    PYTHON (ex.: `base.user_id != user_id`) falhariam silenciosamente com str."""
    return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))


async def load_brain_docs(base_ids: list, user_id) -> list[dict]:
    """Notas dos cérebros `base_ids` com texto decodificado:
    `[{id, base_id, filename, title, text, updated_at}]`."""
    bids = _uuids(base_ids)
    if not bids:
        return []
    uid = _uid(user_id)
    async with SessionLocal() as db:
        docs = list(await db.scalars(
            select(KnowledgeDoc).where(
                KnowledgeDoc.base_id.in_(bids), KnowledgeDoc.user_id == uid
            ).order_by(KnowledgeDoc.filename)
        ))
    out: list[dict] = []
    for d in docs:
        if not _is_note(d):
            continue
        out.append({
            "id": str(d.id),
            "base_id": str(d.base_id),
            "filename": d.filename,
            "title": _doc_title(d),
            "text": bytes(d.data or b"").decode("utf-8", errors="replace"),
            "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        })
    return out


async def resolve_note(user_id, base_ids: list, title: str) -> dict | None:
    """Nota cujo nome de arquivo (ou meta.title) casa com `title` por `note_key`.
    Retorna o dict da nota (como em `load_brain_docs`) ou None."""
    key = note_key(title)
    if not key:
        return None
    docs = await load_brain_docs(base_ids, user_id)
    for d in docs:
        if note_key(d["filename"]) == key:
            return d
    for d in docs:
        if note_key(d["title"]) == key:
            return d
    return None


async def write_note(
    user_id, base_id, title: str, content: str, mode: str = "replace",
) -> dict:
    """Cria/atualiza uma nota `.md` no cérebro `base_id` e agenda a reindexação.

    Upsert por `note_key(title)` (só dentro deste cérebro). `mode="append"`
    concatena ao fim da nota existente. Retorna
    `{doc_id, base_id, title, action: created|updated, size}`. Levanta ValueError
    em título vazio/base inexistente."""
    clean_title = _FILENAME_BAD.sub(" ", (title or "").strip())
    clean_title = re.sub(r"\s+", " ", clean_title).strip()[:200]
    if not clean_title:
        raise ValueError("note title is required")
    bid = _uuids([base_id])
    if not bid:
        raise ValueError("invalid brain id")
    uid = _uid(user_id)
    key = note_key(clean_title)

    async with SessionLocal() as db:
        base = await db.get(KnowledgeBase, bid[0])
        if base is None or base.user_id != uid or base.kind != "brain":
            raise ValueError("brain not found")
        docs = list(await db.scalars(
            select(KnowledgeDoc).where(
                KnowledgeDoc.base_id == bid[0], KnowledgeDoc.user_id == uid
            )
        ))
        existing = next(
            (d for d in docs if _is_note(d) and note_key(d.filename) == key), None
        ) or next(
            (d for d in docs if _is_note(d) and note_key(_doc_title(d)) == key), None
        )
        body = content or ""
        if existing is not None:
            if mode == "append":
                old = bytes(existing.data or b"").decode("utf-8", errors="replace")
                body = (old.rstrip() + "\n\n" + body.strip()) if old.strip() else body
            data = body.encode("utf-8")
            existing.data = data
            existing.size = len(data)
            existing.status = "pending"
            existing.error = None
            doc, action = existing, "updated"
        else:
            data = body.encode("utf-8")
            doc = KnowledgeDoc(
                base_id=bid[0], user_id=uid,
                filename=clean_title + ".md", mime="text/markdown",
                size=len(data), status="pending", chunk_count=0, data=data,
            )
            db.add(doc)
            action = "created"
        await db.commit()
        await db.refresh(doc)
        doc_id = doc.id

    ingest.spawn_index(doc_id)
    return {
        "doc_id": str(doc_id), "base_id": str(bid[0]),
        "title": clean_title, "action": action, "size": len(data),
    }
