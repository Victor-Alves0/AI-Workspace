"""Rotas de COMPACTAÇÃO de contexto: resumir a conversa (não-destrutivo, com
checkpoint restaurável) + gerenciamento dos checkpoints. Montado sob o router
/chats (ver routes.py)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_approved
from ..db import get_db
from ..models import ChatCompaction, Message, User
from .compaction_service import run_compaction, serialize_message, summary_content
from .turn_setup import _get_owned_chat, _ordered_messages

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/{chat_id}/compact")
async def compact_chat(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Compacta o contexto: envia a conversa ao modelo, pede um resumo e substitui
    as mensagens por esse resumo (mantendo o contexto essencial em menos tokens).
    Manual = resume TUDO (keep_last=0); a auto-compactação usa o mesmo núcleo com
    keep_last (ver compaction_service)."""
    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Chat sem modelo definido")
    try:
        res = await run_compaction(db, user, chat, keep_last=0, min_convo=3)
    except Exception as exc:  # noqa: BLE001 - falha no resumo
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao resumir: {exc}")
    if res is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Conversa curta demais para compactar")
    return res


class CompactionOut(BaseModel):
    id: uuid.UUID
    summary: str
    message_count: int
    pinned: bool
    parent_id: uuid.UUID | None = None
    name: str | None = None
    last_message: str | None = None  # preview da última mensagem do snapshot
    created_at: datetime

    class Config:
        from_attributes = True


class CompactionRename(BaseModel):
    name: str | None = Field(default=None, max_length=120)


def _last_message_preview(snapshot: list | None) -> str | None:
    """Preview da última mensagem 'real' do snapshot (p/ busca/exibição na árvore)."""
    if not snapshot:
        return None
    for m in reversed(snapshot):
        if isinstance(m, dict) and not m.get("is_summary"):
            c = (m.get("content") or "").strip()
            if c:
                return c[:140]
    return None


@router.get("/{chat_id}/compactions", response_model=list[CompactionOut])
async def list_compactions(
    chat_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Grafo de checkpoints do chat (árvore via parent_id; mais antigos primeiro)."""
    await _get_owned_chat(db, chat_id, user)
    rows = list(
        await db.scalars(
            select(ChatCompaction)
            .where(ChatCompaction.chat_id == chat_id)
            .order_by(ChatCompaction.created_at.asc())
        )
    )
    return [
        CompactionOut(
            id=c.id,
            summary=c.summary,
            message_count=c.message_count,
            pinned=c.pinned,
            parent_id=c.parent_id,
            name=c.name,
            last_message=_last_message_preview(c.snapshot),
            created_at=c.created_at,
        )
        for c in rows
    ]


@router.patch("/{chat_id}/compactions/{compaction_id}", response_model=CompactionOut)
async def rename_compaction(
    chat_id: uuid.UUID,
    compaction_id: uuid.UUID,
    body: CompactionRename,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Renomeia (nome personalizado) um checkpoint."""
    await _get_owned_chat(db, chat_id, user)
    cp = await db.get(ChatCompaction, compaction_id)
    if cp is None or cp.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkpoint não encontrado")
    cp.name = (body.name or "").strip()[:120] or None
    await db.commit()
    await db.refresh(cp)
    return CompactionOut(
        id=cp.id, summary=cp.summary, message_count=cp.message_count, pinned=cp.pinned,
        parent_id=cp.parent_id, name=cp.name, last_message=_last_message_preview(cp.snapshot),
        created_at=cp.created_at,
    )


def _restore_snapshot_messages(snapshot: list) -> list[Message]:
    """Reconstrói objetos Message a partir de um snapshot de checkpoint."""
    out: list[Message] = []
    for msg in snapshot or []:
        if not isinstance(msg, dict):
            continue
        m = Message(
            role=msg.get("role") or "assistant",
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls"),
            tool_call_id=msg.get("tool_call_id"),
            tokens=msg.get("tokens"),
            cost=msg.get("cost"),
            usage=msg.get("usage"),
            reasoning=msg.get("reasoning"),
            tool_events=msg.get("tool_events"),
            attachments=msg.get("attachments"),
            is_summary=bool(msg.get("is_summary", False)),
            compacted=bool(msg.get("compacted", False)),
        )
        ca = msg.get("created_at")
        if ca:
            try:
                m.created_at = datetime.fromisoformat(ca)
            except (ValueError, TypeError):
                pass
        out.append(m)
    return out


@router.delete("/{chat_id}/compactions/{compaction_id}")
async def delete_compaction(
    chat_id: uuid.UUID,
    compaction_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Remove um checkpoint do grafo.

    - Checkpoint NÃO ativo: some do grafo, sem tocar na conversa atual.
    - Checkpoint ATIVO (fixado): DESCOMPACTA — restaura o estado que existia antes
      desta compactação (as mensagens voltam ao contexto da IA) e o checkpoint pai
      passa a ser o ativo. É o "desfazer" da última compactação."""
    await _get_owned_chat(db, chat_id, user)
    cp = await db.get(ChatCompaction, compaction_id)
    if cp is None or cp.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkpoint não encontrado")

    if not cp.pinned:
        await db.delete(cp)
        await db.commit()
        return {"ok": True, "undone": False}

    # ativo: descompactar. Sem snapshot (checkpoint legado) não há como restaurar.
    if not cp.snapshot:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Este checkpoint ativo não tem estado salvo para desfazer",
        )
    await db.execute(delete(Message).where(Message.chat_id == chat_id))
    for m in _restore_snapshot_messages(cp.snapshot):
        m.chat_id = chat_id
        db.add(m)
    parent_id = cp.parent_id
    await db.delete(cp)
    if parent_id is not None:
        parent = await db.get(ChatCompaction, parent_id)
        if parent is not None and parent.chat_id == chat_id:
            parent.pinned = True
    await db.commit()
    return {"ok": True, "undone": True}


@router.post("/{chat_id}/compactions/{compaction_id}/pin")
async def restore_compaction(
    chat_id: uuid.UUID,
    compaction_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """RESTAURA um checkpoint: substitui as mensagens atuais pelo snapshot completo
    daquele ponto, trazendo a conversa exatamente como estava antes daquela
    compactação. (Checkpoints antigos sem snapshot: cai no fallback de trocar o
    resumo ativo.)"""
    await _get_owned_chat(db, chat_id, user)
    cp = await db.get(ChatCompaction, compaction_id)
    if cp is None or cp.chat_id != chat_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compactação não encontrada")

    # RAMIFICAÇÃO: antes de restaurar, salva o estado ATUAL como um novo checkpoint
    # (ramo), para não perder a conversa em que você está — "mantém os dois".
    prev_pinned = await db.scalar(
        select(ChatCompaction.id).where(
            ChatCompaction.chat_id == chat_id, ChatCompaction.pinned.is_(True)
        )
    )
    cur_rows = await _ordered_messages(db, chat_id)
    convo_n = len([m for m in cur_rows if m.role in ("user", "assistant") and (m.content or "").strip()])
    if cur_rows and prev_pinned != cp.id and convo_n > 0:
        cur_snapshot = [serialize_message(m) for m in cur_rows]
        branch = ChatCompaction(
            chat_id=chat_id,
            summary=_last_message_preview(cur_snapshot) or "Ramo salvo ao restaurar",
            message_count=convo_n,
            pinned=False,
            parent_id=prev_pinned,
            name="Ramo (auto)",
            snapshot=cur_snapshot,
        )
        db.add(branch)

    await db.execute(
        update(ChatCompaction).where(ChatCompaction.chat_id == chat_id).values(pinned=False)
    )
    cp.pinned = True

    snapshot = cp.snapshot or []
    if snapshot:
        # restaura o ponto: apaga o estado atual e recria as mensagens do snapshot
        await db.execute(delete(Message).where(Message.chat_id == chat_id))
        for msg in snapshot:
            if not isinstance(msg, dict):
                continue
            m = Message(
                chat_id=chat_id,
                role=msg.get("role") or "assistant",
                content=msg.get("content") or "",
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                tokens=msg.get("tokens"),
                cost=msg.get("cost"),
                usage=msg.get("usage"),
                reasoning=msg.get("reasoning"),
                tool_events=msg.get("tool_events"),
                attachments=msg.get("attachments"),
                is_summary=bool(msg.get("is_summary", False)),
                compacted=bool(msg.get("compacted", False)),
            )
            ca = msg.get("created_at")
            if ca:
                try:
                    m.created_at = datetime.fromisoformat(ca)
                except (ValueError, TypeError):
                    pass
            db.add(m)
        await db.commit()
        return {"ok": True, "restored": len(snapshot)}

    # fallback (checkpoint legado sem snapshot): troca só a mensagem-resumo do topo
    summary_msg = await db.scalar(
        select(Message)
        .where(Message.chat_id == chat_id, Message.is_summary.is_(True))
        .order_by(Message.created_at)
        .limit(1)
    )
    if summary_msg is not None:
        summary_msg.content = summary_content(cp.summary)
    else:
        earliest = await db.scalar(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at).limit(1)
        )
        note = Message(
            chat_id=chat_id, role="assistant", content=summary_content(cp.summary), is_summary=True
        )
        if earliest is not None:
            note.created_at = earliest.created_at - timedelta(seconds=1)
        db.add(note)
    await db.commit()
    return {"ok": True, "restored": 0}


