"""Enriquecedor com IA da Base de Conhecimento.

Gera título/descrição/tags para os documentos e grava como PROPOSTA
(`KnowledgeEnrichment`) — o usuário aprova (vira `KnowledgeDoc.meta` + reindexa via
`ingest.index_doc`) ou descarta. NUNCA escreve o meta direto.

- imagens → modelo de VISÃO (a imagem vai como data-url);
- documentos → texto extraído (truncado) para o modelo resumir;
- vídeo/áudio → a partir do nome do arquivo (transcrição local fica p/ um passo futuro).

Roda em segundo plano (uma sessão por doc), então uma base grande não trava a request.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import uuid

from ..db import SessionLocal
from ..models import KnowledgeDoc, KnowledgeEnrichment
from ..providers import openrouter
from .. import extraction
from . import ingest

logger = logging.getLogger(__name__)

_MAX_DOC_CHARS = 6000     # texto de doc enviado ao modelo (o resto não muda o resumo)
_IMG_MAX_BYTES = 1_500_000  # acima disso, reduz a imagem antes de mandar (custo/payload)
_IMG_MAX_DIM = 1024

_INSTRUCT = (
    "You organize a personal knowledge base. For the file below, produce concise "
    "metadata to make it findable later. Reply with ONLY a JSON object, no prose, no "
    "code fences:\n"
    '{"title": "<short human title>", "description": "<1-2 sentence description of what '
    'this file is/shows>", "tags": ["<3-8 short lowercase keywords>"]}\n'
    "Write in the same language as the file's content or name. Be specific and concrete "
    "(names, subjects, topics visible), not generic."
)


def _downscale_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Reduz imagens grandes antes de mandar ao modelo (mais barato e rápido). Falha
    graciosamente devolvendo os bytes originais."""
    if len(data) <= _IMG_MAX_BYTES:
        return data, mime
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.thumbnail((_IMG_MAX_DIM, _IMG_MAX_DIM))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - imagem exótica: manda como está
        return data, mime


def _parse_proposal(txt: str) -> dict:
    """Extrai {title, description, tags} do texto do modelo (tolerante a fences/ruído)."""
    s = (txt or "").strip()
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
    tags = obj.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
    tags = [str(t).strip().lower() for t in tags if str(t).strip()][:8]
    return {
        "title": str(obj.get("title") or "").strip()[:200],
        "description": str(obj.get("description") or "").strip()[:1000],
        "tags": tags,
    }


async def _proposal_for_doc(doc: KnowledgeDoc, model: str, api_key: str,
                            base_url: str | None, extra_prompt: str) -> dict:
    """Gera a proposta de metadados para UM doc (levanta em erro)."""
    filename, mime = doc.filename or "arquivo", doc.mime or ""
    extra = f"\n\nExtra instruction from the user: {extra_prompt.strip()}" if (extra_prompt or "").strip() else ""

    if ingest.is_image(filename, mime):
        raw, send_mime = _downscale_image(bytes(doc.data or b""), mime)
        b64 = base64.b64encode(raw).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": _INSTRUCT + extra + f"\n\nFilename: {filename}"},
            {"type": "image_url", "image_url": {"url": f"data:{send_mime};base64,{b64}"}},
        ]}]
    elif ingest.is_video(filename, mime):
        messages = [{"role": "user", "content": (
            _INSTRUCT + extra + f"\n\nThis is a video/audio file named: {filename}. "
            "Infer a clean title, a short description and tags from the filename."
        )}]
    else:
        try:
            text = extraction.extract(filename, mime, bytes(doc.data or b""))
        except Exception:  # noqa: BLE001
            text = ""
        body = (text or "")[:_MAX_DOC_CHARS] or "(no extractable text)"
        messages = [{"role": "user", "content": (
            _INSTRUCT + extra + f"\n\nFilename: {filename}\n\nContent:\n{body}"
        )}]

    out = await openrouter.complete(
        api_key, model, messages, params={"temperature": 0.2}, base_url=base_url, timeout=90.0
    )
    prop = _parse_proposal(out)
    if not (prop.get("title") or prop.get("description") or prop.get("tags")):
        raise ValueError("o modelo não retornou metadados válidos")
    return prop


async def run_job(enr_ids: list[uuid.UUID], model: str, api_key: str, base_url: str | None) -> None:
    """Processa cada proposta pendente: gera os metadados e marca ready/error. Uma
    sessão por doc — a lista some/aparece na UI conforme conclui."""
    for eid in enr_ids:
        async with SessionLocal() as db:
            enr = await db.get(KnowledgeEnrichment, eid)
            if enr is None or enr.status != "pending":
                continue
            doc = await db.get(KnowledgeDoc, enr.doc_id)
            extra = enr.extra_prompt or ""
        if doc is None or not doc.data:
            async with SessionLocal() as db:
                e = await db.get(KnowledgeEnrichment, eid)
                if e is not None:
                    e.status, e.error = "error", "documento sem conteúdo"
                    await db.commit()
            continue
        try:
            prop = await _proposal_for_doc(doc, model, api_key, base_url, extra)
            async with SessionLocal() as db:
                e = await db.get(KnowledgeEnrichment, eid)
                if e is not None and e.status == "pending":
                    e.title = prop.get("title") or ""
                    e.description = prop.get("description") or ""
                    e.tags = prop.get("tags") or []
                    e.status = "ready"
                    await db.commit()
        except Exception as exc:  # noqa: BLE001 - um doc que falha não derruba o lote
            logger.warning("enrich: doc %s falhou: %s", getattr(doc, "id", "?"), exc)
            async with SessionLocal() as db:
                e = await db.get(KnowledgeEnrichment, eid)
                if e is not None and e.status == "pending":
                    e.status, e.error = "error", str(exc)[:500]
                    await db.commit()


async def apply_to_doc(db, doc: KnowledgeDoc, enr: KnowledgeEnrichment) -> None:
    """Funde a proposta aprovada no `meta` do doc e marca para reindexar (o chamador
    dispara `ingest.index_doc` — os metadados entram no embedding via `_meta_prefix`,
    então a busca passa a achar o item)."""
    meta = dict(doc.meta or {})
    if enr.title:
        meta["title"] = enr.title
    if enr.description:
        meta["description"] = enr.description
    if enr.tags:
        # une com as tags já existentes (sem duplicar), preservando o que o usuário pôs
        existing = [str(t) for t in (meta.get("tags") or [])]
        meta["tags"] = existing + [t for t in enr.tags if t not in existing]
    doc.meta = meta
    if doc.data:
        doc.status = "pending"  # o chamador reindexa via _spawn_index
