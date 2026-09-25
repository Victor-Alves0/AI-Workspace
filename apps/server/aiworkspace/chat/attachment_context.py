"""Anexos no HISTÓRICO: o que a IA continua vendo dos arquivos enviados em mensagens anteriores.

Antes, um anexo só existia para o modelo no turno em que foi enviado; nas mensagens
seguintes o histórico levava apenas o texto digitado (e uma mensagem só com anexo
sumia). Agora, como no Claude/ChatGPT:

- arquivo PEQUENO (texto colado, documento curto): volta inteiro em todo turno. O prefixo
  repetido é barato com o cache de prompt;
- arquivo GRANDE, ou além do orçamento total do histórico (os mais recentes têm
  prioridade): vira uma linha de referência, e a IA reabre com a ferramenta
  `read_attachment` (ler por partes ou buscar um trecho);
- imagem: as das últimas mensagens voltam como imagem (modelos com visão; `_images`,
  aplicado no orquestrador), as antigas viram uma linha de referência;
- áudio: linha de referência.

As chaves privadas `_images`/`_files` saem da mensagem antes de ir ao provedor.
"""
from __future__ import annotations

import base64
import logging
import re
import uuid
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from ..providers import reasoning_details as _reasoning_details

logger = logging.getLogger(__name__)

INLINE_CHARS = 32_000          # ~8k tokens: até aqui o arquivo volta inteiro
HISTORY_INLINE_BUDGET = 120_000  # soma dos arquivos inteiros no histórico
RECENT_IMAGE_MESSAGES = 2       # mensagens com imagem que voltam como imagem
READ_CHUNK = 12_000             # quanto `read_attachment` devolve por vez


def _atts(m: Any) -> list[dict]:
    a = getattr(m, "attachments", None)
    return [x for x in a if isinstance(x, dict)] if isinstance(a, list) else []


def keep(m: Any) -> bool:
    """A mensagem entra no histórico? (texto OU anexo; antes, só com texto)."""
    return m.role in ("user", "assistant") and not getattr(m, "compacted", False) \
        and bool((m.content or "").strip() or (m.role == "user" and _atts(m)))


async def _upload_rows(ids: set[str]) -> dict[str, Any]:
    from ..db import SessionLocal
    from ..models import Upload

    uids = []
    for i in ids:
        try:
            uids.append(uuid.UUID(i))
        except (ValueError, TypeError):
            continue
    if not uids:
        return {}
    async with SessionLocal() as s:
        rows = await s.scalars(select(Upload).where(Upload.id.in_(uids)))
        return {str(r.id): r for r in rows}


async def _image_url(a: dict, rows: dict[str, Any]) -> str | None:
    url = str(a.get("url") or "")
    if url.startswith("data:"):
        return url
    row = rows.get(str(a.get("upload_id") or ""))
    if row is None:
        return None
    from .. import uploads_service

    data = await run_in_threadpool(uploads_service.read_bytes, row, uploads_service.max_bytes_for("image"))
    if data is None:
        return None
    return f"data:{row.mime or 'image/png'};base64,{base64.b64encode(data).decode()}"


async def history(messages: list[Any]) -> list[dict[str, Any]]:
    """Mensagens gravadas (já filtradas e em ordem) → histórico para o modelo, com os anexos."""
    msgs = [m for m in messages if keep(m)]
    ids = {str(a["upload_id"]) for m in msgs for a in _atts(m) if a.get("upload_id")}
    rows = await _upload_rows(ids) if ids else {}

    # imagens recentes voltam como imagem; o orçamento de texto vai dos mais novos aos mais velhos
    recent_img: set[int] = set()
    for i in range(len(msgs) - 1, -1, -1):
        if len(recent_img) >= RECENT_IMAGE_MESSAGES:
            break
        if msgs[i].role == "user" and any(a.get("type") == "image" for a in _atts(msgs[i])):
            recent_img.add(i)
    budget = HISTORY_INLINE_BUDGET
    blocks_by_msg: dict[int, list[str]] = {}
    files_by_msg: dict[int, list[str]] = {}
    images_by_msg: dict[int, list[str]] = {}
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.role != "user":
            continue
        blocks: list[str] = []
        for a in _atts(m):
            kind = a.get("type") or "file"
            row = rows.get(str(a.get("upload_id") or ""))
            name = str(a.get("name") or (row.filename if row is not None else "") or "arquivo")
            if kind == "image":
                url = await _image_url(a, rows) if i in recent_img else None
                if url:
                    images_by_msg.setdefault(i, []).append(url)
                blocks.append(f"[Imagem anexada: {name}]")
                continue
            if kind == "audio":
                blocks.append(f"[Áudio anexado: {name}]")
                continue
            text = a.get("text") if isinstance(a.get("text"), str) else (row.text if row is not None else None)
            files_by_msg.setdefault(i, []).append(name)
            if not text:
                blocks.append(f"[Arquivo anexado: {name} — sem texto legível]")
            elif len(text) <= INLINE_CHARS and len(text) <= budget:
                budget -= len(text)
                blocks.append(f"[Arquivo anexado: {name}]\n{text}")
            else:
                blocks.append(
                    f"[Arquivo anexado: {name} — {len(text):,} caracteres, não repetido aqui para "
                    f"poupar contexto. Use read_attachment(name=\"{name}\") para reler, ler por "
                    "partes ou buscar um trecho.]".replace(",", ".")
                )
        if blocks:
            blocks_by_msg[i] = blocks

    out: list[dict[str, Any]] = []
    for i, m in enumerate(msgs):
        e = _reasoning_details.history_entry(m)
        if i in blocks_by_msg:
            e["content"] = "\n\n".join(p for p in [(m.content or "").strip(), *blocks_by_msg[i]] if p)
        if i in files_by_msg:
            e["_files"] = files_by_msg[i]
        if i in images_by_msg:
            e["_images"] = images_by_msg[i]
        out.append(e)
    return out


def for_provider(history: list[dict[str, Any]], vision: bool) -> list[dict[str, Any]]:
    """Tira as chaves privadas; com visão, as imagens recentes voltam como partes image_url."""
    out: list[dict[str, Any]] = []
    for m in history:
        if "_images" not in m and "_files" not in m:
            out.append(m)
            continue
        e = {k: v for k, v in m.items() if k not in ("_images", "_files")}
        imgs = m.get("_images") or []
        if vision and imgs and isinstance(e.get("content"), str):
            e["content"] = [{"type": "text", "text": e["content"]}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in imgs
            ]
        out.append(e)
    return out


def has_files(history: list[dict[str, Any]]) -> bool:
    return any(isinstance(m, dict) and m.get("_files") for m in history or [])


def read_attachment_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "read_attachment",
            "description": (
                "Re-read a file the user attached earlier in this chat (by its name). Use it when "
                "a file in the history is marked as not repeated, or when you need its exact text. "
                "Read it in parts with `offset`, or pass `query` to get the passages that match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "the attached file's name"},
                    "offset": {"type": "integer", "description": "character to start reading from (default 0)"},
                    "query": {"type": "string", "description": "optional: return only the passages containing this text"},
                },
                "required": ["name"],
            },
        },
    }


async def read_attachment(user_id: str | None, chat_id: str | None, args: dict) -> dict[str, Any]:
    """Texto de um anexo deste chat, por partes ou por busca."""
    from ..db import SessionLocal
    from ..models import Chat, Message

    want = str(args.get("name") or "").strip().lower()
    if not want or not chat_id:
        return {"error": "informe `name` (o nome do arquivo anexado)"}
    try:
        cid = uuid.UUID(str(chat_id))
        uid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        return {"error": "chat inválido"}
    async with SessionLocal() as s:
        chat = await s.get(Chat, cid)
        if chat is None or chat.user_id != uid:
            return {"error": "chat não encontrado"}
        msgs = list(await s.scalars(
            select(Message).where(Message.chat_id == cid, Message.role == "user",
                                  Message.attachments.isnot(None))
            .order_by(Message.created_at.desc())
        ))
    candidatos: list[tuple[str, dict]] = []
    for m in msgs:
        for a in _atts(m):
            if (a.get("type") or "file") != "file":
                continue
            candidatos.append((str(a.get("name") or ""), a))
    nomes = [n for n, _ in candidatos]
    escolha = next((a for n, a in candidatos if n.lower() == want), None) \
        or next((a for n, a in candidatos if want in n.lower()), None)
    if escolha is None:
        return {"error": f"nenhum anexo chamado '{args.get('name')}' neste chat",
                "available": sorted(set(nomes))[:50]}
    text = escolha.get("text") if isinstance(escolha.get("text"), str) else None
    if text is None and escolha.get("upload_id"):
        rows = await _upload_rows({str(escolha["upload_id"])})
        row = rows.get(str(escolha["upload_id"]))
        text = row.text if row is not None else None
    if not text:
        return {"error": "este anexo não tem texto legível"}
    return excerpt(str(escolha.get("name") or ""), text, args)


def excerpt(nome: str, text: str, args: dict) -> dict[str, Any]:
    """Uma parte do texto (a partir de `offset`) ou os trechos que contêm `query`."""
    query = str(args.get("query") or "").strip()
    if query:
        trechos = []
        for mt in re.finditer(re.escape(query), text, re.IGNORECASE):
            ini, fim = max(0, mt.start() - 400), min(len(text), mt.end() + 400)
            if trechos and ini <= trechos[-1][1]:
                trechos[-1] = (trechos[-1][0], fim)
            else:
                trechos.append((ini, fim))
            if len(trechos) >= 12:
                break
        return {"name": nome, "total_chars": len(text), "matches": len(trechos),
                "passages": [{"offset": a, "text": text[a:b]} for a, b in trechos]}
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    parte = text[offset: offset + READ_CHUNK]
    fim = offset + len(parte)
    return {"name": nome, "total_chars": len(text), "offset": offset, "text": parte,
            **({"next_offset": fim} if fim < len(text) else {"end": True})}
