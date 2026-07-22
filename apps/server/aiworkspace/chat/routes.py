"""Rotas de chat — GERENCIAMENTO (CRUD, compartilhar, informações, clone, bulk).

O antigo monólito foi dividido:
  - turn_setup.py          → helpers de preparação de turno (sem rotas)
  - messages_routes.py     → envio/stream/regenerar/continuar/efêmero
  - roundtable_routes.py   → mesa-redonda (multi-model chat)
  - compaction_routes.py   → compactação de contexto + checkpoints
Os sub-routers são incluídos no `router` deste módulo (prefixo /chats único).
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto
from ..auth.deps import require_approved
from ..auth.security import hash_password
from ..db import get_db
from ..models import Artifact, Chat, CodespaceProject, Message, User
from ..schemas.chat import (
    ChatCreate,
    ChatDetail,
    ChatOut,
    ChatUpdate,
    MessageOut,
)
from ..secrets_service import OPENROUTER_KEY, get_secret
from . import compaction_routes, messages_routes, roundtable_routes
from .turn_setup import _get_owned_chat

# Compat: símbolos históricos re-exportados — importadores externos (automation,
# integrations, playground, knowledge_routes) usavam `chat.routes._*`. Código
# novo deve importar de `chat.turn_setup`.
from .turn_setup import (  # noqa: F401
    _audio_router_config,
    _code_mode,
    _get_model_config,
    _load_skills,
    _mem_agent_id,
    _prepare_turn,
    _resolve_guards,
    _resolve_knowledge,
    _resolve_provider,
    _tz_from_header,
    _usage_record,
    _use_context,
    _user_profile_dict,
)

router = APIRouter(prefix="/chats", tags=["chats"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[ChatOut])
async def list_chats(
    archived: bool = False,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Chat)
        .where(Chat.user_id == user.id, Chat.archived == archived)
        .order_by(Chat.pinned.desc(), Chat.updated_at.desc())
    )
    return list(rows)


async def _owned_project_id(db: AsyncSession, user: User, project_id: uuid.UUID | None) -> uuid.UUID | None:
    """Só devolve o project_id se pertencer a este usuário — o Codespace expõe
    CÓDIGO-FONTE via tool; vincular o chat ao projeto de outro usuário vazaria
    arquivos que não são dele. Diferente de folder_id/model_config_id (presets
    de UI, sem esse risco), este vínculo é validado explicitamente aqui."""
    if project_id is None:
        return None
    p = await db.get(CodespaceProject, project_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projeto do Codespace não encontrado")
    return project_id


@router.post("", response_model=ChatOut)
async def create_chat(
    body: ChatCreate, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    project_id = await _owned_project_id(db, user, body.project_id)
    memory_config: dict | None = None
    if project_id is not None:
        # o chat nasce lendo/escrevendo o banco de memória do projeto (ver
        # codespace_routes.create_project) — contexto já compartilhado entre
        # os chats do mesmo Codespace, sem precisar acoplar manualmente.
        proj = await db.get(CodespaceProject, project_id)
        if proj is not None and proj.memory_bank_id:
            memory_config = {"banks": [str(proj.memory_bank_id)]}
    chat = Chat(
        user_id=user.id,
        title=body.title,
        model=body.model,
        system_prompt=body.system_prompt,
        params=body.params,
        folder_id=body.folder_id,
        model_config_id=body.model_config_id,
        project_id=project_id,
        memory_config=memory_config,
    )
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return chat


class SharedChatOut(BaseModel):
    id: str
    title: str
    public_id: str
    url: str
    has_password: bool
    expires_at: datetime | None
    expired: bool
    created_at: datetime
    updated_at: datetime


def _shared_out(c: Chat, now: datetime) -> SharedChatOut:
    return SharedChatOut(
        id=str(c.id),
        title=c.title,
        public_id=c.public_id or "",
        url=f"/shared/{c.public_id}",
        has_password=bool(c.public_password_hash),
        expires_at=c.public_expires_at,
        expired=bool(c.public_expires_at and c.public_expires_at <= now),
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


# NB: declarado ANTES de `/{chat_id}` senão "shared" cai na rota paramétrica (422).
@router.get("/shared", response_model=list[SharedChatOut])
async def list_shared_chats(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Lista os chats do usuário que têm link público ativo (para gerenciar)."""
    now = datetime.now(timezone.utc)
    rows = await db.scalars(
        select(Chat)
        .where(Chat.user_id == user.id, Chat.public_id.is_not(None))
        .order_by(Chat.updated_at.desc())
    )
    return [_shared_out(c, now) for c in rows]


@router.get("/{chat_id}", response_model=ChatDetail)
async def get_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = await _get_owned_chat(db, chat_id, user)
    await db.refresh(chat, attribute_names=["messages"])
    return chat


@router.patch("/{chat_id}", response_model=ChatOut)
async def update_chat(
    chat_id: uuid.UUID,
    body: ChatUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    chat = await _get_owned_chat(db, chat_id, user)
    fields = body.model_dump(exclude_unset=True)
    if "project_id" in fields:
        fields["project_id"] = await _owned_project_id(db, user, fields["project_id"])
    for field, value in fields.items():
        setattr(chat, field, value)
    await db.commit()
    await db.refresh(chat)
    return chat


@router.post("/{chat_id}/share")
async def share_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Cria (ou retorna) o link público read-only do chat. Idempotente: reusa o
    public_id existente. Devolve o token — o front monta a URL /shared/<token>."""
    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.public_id:
        chat.public_id = secrets.token_urlsafe(12)[:24]
        await db.commit()
    return {"public_id": chat.public_id}


class ShareUpdate(BaseModel):
    # `exclude_unset`: só mexe no que veio no corpo (senha e validade são
    # independentes). password: string define, ""/null limpa. expires_at: ISO
    # define, null limpa.
    password: str | None = None
    expires_at: datetime | None = None


@router.patch("/{chat_id}/share")
async def update_share(
    chat_id: uuid.UUID,
    body: ShareUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Atualiza a senha e/ou a validade do link público (sem trocar o link)."""
    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.public_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este chat não está compartilhado")
    data = body.model_dump(exclude_unset=True)
    if "password" in data:
        pw = (data["password"] or "").strip()
        chat.public_password_hash = hash_password(pw) if pw else None
    if "expires_at" in data:
        chat.public_expires_at = data["expires_at"]
    await db.commit()
    await db.refresh(chat)
    return _shared_out(chat, datetime.now(timezone.utc))


@router.post("/{chat_id}/share/rotate")
async def rotate_share(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Gera um NOVO link (invalida o anterior); mantém senha e validade."""
    chat = await _get_owned_chat(db, chat_id, user)
    if not chat.public_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este chat não está compartilhado")
    chat.public_id = secrets.token_urlsafe(12)[:24]
    await db.commit()
    return {"public_id": chat.public_id, "url": f"/shared/{chat.public_id}"}


@router.delete("/{chat_id}/share")
async def unshare_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Revoga o link público (o chat volta a ser privado). Limpa senha e validade."""
    chat = await _get_owned_chat(db, chat_id, user)
    chat.public_id = None
    chat.public_password_hash = None
    chat.public_expires_at = None
    await db.commit()
    return {"ok": True}


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = await _get_owned_chat(db, chat_id, user)
    await db.delete(chat)
    await db.commit()
    return {"ok": True}


@router.get("/{chat_id}/info")
async def chat_info(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    """Painel "Informações" do chat: modelo, nº de mensagens, tokens (entrada/saída),
    custo total, artefatos e quantas memórias estão vinculadas a esta conversa."""
    chat = await _get_owned_chat(db, chat_id, user)

    msgs = list(await db.scalars(select(Message).where(Message.chat_id == chat_id)))
    tokens_in = tokens_out = 0
    cost = 0.0
    for m in msgs:
        u = m.usage or {}
        tokens_in += int(u.get("prompt_tokens", 0) or 0)
        tokens_out += int(u.get("completion_tokens", 0) or 0)
        cost += float(u.get("cost", 0.0) or (m.cost or 0.0))
    convo_count = sum(1 for m in msgs if m.role in ("user", "assistant") and not m.is_summary)

    arts = list(await db.scalars(
        select(Artifact).where(Artifact.chat_id == chat_id).order_by(Artifact.updated_at.desc())
    ))
    artifacts = [
        {"id": str(a.id), "identifier": a.identifier, "title": a.title or a.identifier,
         "kind": a.kind, "version": a.version}
        for a in arts
    ]

    # memórias vinculadas a este chat (escopo "chat") — melhor-esforço
    memory_count = 0
    try:
        from ..memory import mem0_service
        key = (await get_secret(db, user.id, OPENROUTER_KEY)) or "x"
        rows = await run_in_threadpool(
            lambda: mem0_service.list_memories(key, str(user.id), scope="chat", chat_id=str(chat_id))
        )
        memory_count = len(rows)
    except Exception:  # noqa: BLE001
        memory_count = 0

    return {
        "id": str(chat.id),
        "title": chat.title,
        "model": chat.model,
        "tags": chat.tags or [],
        "message_count": convo_count,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": round(cost, 6),
        "artifacts": artifacts,
        "memory_count": memory_count,
        "created_at": chat.created_at.isoformat() if chat.created_at else None,
        "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
    }


@router.post("/{chat_id}/clone", response_model=ChatOut)
async def clone_chat(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    chat = await _get_owned_chat(db, chat_id, user)
    await db.refresh(chat, attribute_names=["messages"])
    clone = Chat(
        user_id=user.id,
        folder_id=chat.folder_id,
        title=f"{chat.title} (cópia)",
        system_prompt=chat.system_prompt,
        model=chat.model,
        params=chat.params,
    )
    db.add(clone)
    await db.flush()
    for m in chat.messages:
        db.add(
            Message(
                chat_id=clone.id,
                role=m.role,
                content=m.content,
                tool_calls=m.tool_calls,
                tool_call_id=m.tool_call_id,
            )
        )
    await db.commit()
    await db.refresh(clone)
    return clone


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def list_messages(
    chat_id: uuid.UUID, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await _get_owned_chat(db, chat_id, user)
    rows = await db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    return list(rows)


# --------------------------------------------------------------------------- #
# Operações em massa (aba "Controle de Dados" das Configurações)
# --------------------------------------------------------------------------- #
class ChatImportItem(BaseModel):
    title: str = "Chat importado"
    model: str = ""
    system_prompt: str | None = None
    params: dict = {}
    messages: list[dict] = []


class ExportIn(BaseModel):
    # se informado, o export sai CIFRADO com esta senha (AES/Fernet + scrypt)
    password: str | None = Field(default=None, max_length=256)


class ImportIn(BaseModel):
    items: list[ChatImportItem] | None = None
    blob: str | None = None  # export cifrado
    password: str | None = Field(default=None, max_length=256)


@router.post("/bulk/export")
async def export_chats(
    body: ExportIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Exporta todos os chats do usuário. Com senha, devolve um blob cifrado."""
    chats = list(
        await db.scalars(
            select(Chat).where(Chat.user_id == user.id).order_by(Chat.created_at)
        )
    )
    out = []
    for c in chats:
        msgs = await db.scalars(
            select(Message).where(Message.chat_id == c.id).order_by(Message.created_at)
        )
        out.append(
            {
                "title": c.title,
                "model": c.model,
                "system_prompt": c.system_prompt,
                "params": c.params,
                "archived": c.archived,
                "pinned": c.pinned,
                "created_at": c.created_at.isoformat(),
                "messages": [
                    {"role": m.role, "content": m.content, "usage": m.usage}
                    for m in msgs
                ],
            }
        )
    if body.password:
        blob = crypto.encrypt_with_password(
            json.dumps(out, ensure_ascii=False, default=str), body.password
        )
        return {"encrypted": True, "blob": blob}
    return {"encrypted": False, "items": out}


@router.post("/bulk/import")
async def import_chats(
    body: ImportIn,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Importa chats (formato do export, texto ou cifrado por senha)."""
    items = body.items or []
    if body.blob:
        if not body.password:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Este arquivo é cifrado — informe a senha")
        try:
            raw = crypto.decrypt_with_password(body.blob, body.password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        try:
            data = json.loads(raw)
            items = [ChatImportItem(**d) for d in data if isinstance(d, dict)]
        except (ValueError, TypeError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Conteúdo do arquivo inválido")

    created = 0
    for item in items:
        chat = Chat(
            user_id=user.id,
            title=item.title or "Chat importado",
            model=item.model or "",
            system_prompt=item.system_prompt,
            params=item.params or {},
        )
        db.add(chat)
        await db.flush()
        for m in item.messages:
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant", "system", "tool") and content:
                db.add(Message(chat_id=chat.id, role=role, content=content))
        created += 1
    await db.commit()
    return {"ok": True, "imported": created}


@router.post("/bulk/archive-all")
async def archive_all_chats(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await db.execute(
        update(Chat).where(Chat.user_id == user.id).values(archived=True)
    )
    await db.commit()
    return {"ok": True}


@router.post("/bulk/delete-all")
async def delete_all_chats(
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    await db.execute(delete(Chat).where(Chat.user_id == user.id))
    await db.commit()
    return {"ok": True}


# sub-routers (paths relativos; o prefixo /chats vem deste router)
router.include_router(messages_routes.router)
router.include_router(roundtable_routes.router)
router.include_router(compaction_routes.router)
