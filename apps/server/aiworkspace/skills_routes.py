"""Aba Skills: CRUD de skills do usuário.

Skills são documentos (sem execução de código) — disponíveis a qualquer usuário
aprovado. O modelo vê nome+descrição e carrega o conteúdo completo sob demanda via
`view_skill` (lazy loading). No chat, `$slug` invoca uma skill para o turno.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved
from .db import get_db
from .models import Chat, Skill, SkillProposal, User

router = APIRouter(prefix="/skills", tags=["skills"])

_SLUG = r"^[a-z0-9][a-z0-9_]*$"

# arquivos de referência: limites p/ não inchar a linha nem o contexto do modelo
_MAX_FILES = 30
_MAX_FILE_BYTES = 100_000       # por arquivo
_MAX_FILES_BYTES = 600_000      # somados


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower().strip()).strip("_")
    return s or "skill"


class SkillFile(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=_MAX_FILE_BYTES)


class SkillIn(BaseModel):
    slug: str = Field(pattern=_SLUG, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=200_000)
    files: list[SkillFile] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class SkillUpdate(BaseModel):
    slug: str | None = Field(default=None, pattern=_SLUG, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=200_000)
    files: list[SkillFile] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class SkillOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    content: str
    files: list[SkillFile]
    tags: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = t.strip().lower()[:40]
        if t and t not in out:
            out.append(t)
    return out[:10]


# caminho de referência seguro: relativo, sem subir de diretório nem barra inicial
_UNSAFE_SEG = ("..", "")


def _clean_file_name(name: str) -> str:
    """Normaliza o nome/caminho de um arquivo de referência para algo seguro e
    relativo (ex.: 'references/palette.md'). Descarta drive/UNC, '..' e barra inicial."""
    n = (name or "").strip().replace("\\", "/").lstrip("/")
    segs = [s.strip() for s in n.split("/") if s.strip() and s.strip() != "."]
    segs = [s for s in segs if s not in _UNSAFE_SEG]
    return "/".join(segs)[:200]


def _clean_files(files: list) -> list[dict]:
    """Valida/normaliza a lista de arquivos de referência: nomes seguros, únicos,
    dentro dos limites de tamanho. Aceita SkillFile ou dict cru."""
    out: list[dict] = []
    seen: set[str] = set()
    total = 0
    for f in files or []:
        if isinstance(f, SkillFile):
            raw_name, raw_content = f.name, f.content
        elif isinstance(f, dict):
            raw_name, raw_content = f.get("name", ""), f.get("content", "")
        else:
            continue
        nm = _clean_file_name(str(raw_name))
        if not nm or nm.lower() in seen:
            continue
        content = str(raw_content or "")[:_MAX_FILE_BYTES]
        total += len(content)
        if total > _MAX_FILES_BYTES:
            break
        seen.add(nm.lower())
        out.append({"name": nm, "content": content})
        if len(out) >= _MAX_FILES:
            break
    return out


@router.get("", response_model=list[SkillOut])
async def list_skills(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Skill).where(Skill.user_id == user.id).order_by(Skill.name)
    )
    return list(rows)


@router.post("", response_model=SkillOut)
async def create_skill(
    body: SkillIn, user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)
):
    data = body.model_dump()
    data["slug"] = data["slug"] or _slugify(data["name"])
    data["tags"] = _clean_tags(data["tags"])
    data["files"] = _clean_files(body.files)
    skill = Skill(user_id=user.id, **data)
    db.add(skill)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe uma skill com esse identificador")
    await db.refresh(skill)
    return skill


async def _owned(db: AsyncSession, skill_id: uuid.UUID, user: User) -> Skill:
    s = await db.get(Skill, skill_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill não encontrada")
    return s


@router.patch("/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdate,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    s = await _owned(db, skill_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "tags" and value is not None:
            value = _clean_tags(value)
        elif field == "files" and value is not None:
            value = _clean_files(value)
        setattr(s, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe uma skill com esse identificador")
    await db.refresh(s)
    return s


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    s = await _owned(db, skill_id, user)
    await db.delete(s)
    await db.commit()
    return {"ok": True}


_MAX_UPLOAD_BYTES = 5_000_000


@router.post("/import", response_model=list[SkillOut])
async def import_skills(
    file: UploadFile = File(...),
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Importa skill(s) de um arquivo enviado: `.skill`/`.zip` (bundle com SKILL.md +
    references/*), `.json` (o nosso export — objeto ou lista) ou `.md` (um SKILL.md).
    Cria direto (slug único por skill) e devolve as criadas."""
    from .integrations import library_import

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "arquivo vazio")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "arquivo muito grande (máx. 5MB)")
    try:
        parsed = library_import.parse_skill_upload(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    created: list[Skill] = []
    for sk in parsed:
        slug = await _unique_slug(db, user, sk.get("slug") or sk.get("name") or "skill")
        skill = Skill(
            user_id=user.id, slug=slug, name=(sk.get("name") or "skill")[:255],
            description=(sk.get("description") or "")[:2000],
            content=(sk.get("content") or "")[:200_000],
            files=_clean_files(list(sk.get("files") or [])),
            tags=_clean_tags(list(sk.get("tags") or [])), enabled=True,
        )
        db.add(skill)
        created.append(skill)
    await db.commit()
    for s in created:
        await db.refresh(s)
    return created


# --------------------------------------------------------------------------- #
# Propostas de skill do Aprendizado Proativo (Curator): fila para aprovação.
# --------------------------------------------------------------------------- #
class ProposalOut(BaseModel):
    id: uuid.UUID
    chat_id: uuid.UUID | None
    chat_title: str | None = None   # título da conversa de origem (para "de onde veio")
    name: str
    slug: str
    description: str
    content: str
    files: list[SkillFile] = Field(default_factory=list)
    rationale: str = ""             # por que a IA sugeriu (o gatilho na conversa)
    tags: list[str]
    source: str
    created_at: datetime

    class Config:
        from_attributes = True


async def _owned_proposal(db: AsyncSession, pid: uuid.UUID, user: User) -> SkillProposal:
    p = await db.get(SkillProposal, pid)
    if p is None or p.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposta não encontrada")
    return p


@router.get("/proposals", response_model=list[ProposalOut])
async def list_proposals(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = list(await db.scalars(
        select(SkillProposal)
        .where(SkillProposal.user_id == user.id, SkillProposal.status == "pending")
        .order_by(SkillProposal.created_at.desc())
    ))
    # títulos das conversas de origem (para "de qual conversa veio")
    cids = [p.chat_id for p in rows if p.chat_id]
    titles: dict[uuid.UUID, str] = {}
    if cids:
        titles = dict(
            (cid, title)
            for cid, title in (
                await db.execute(select(Chat.id, Chat.title).where(Chat.id.in_(cids)))
            ).all()
        )
    return [
        ProposalOut(
            id=p.id, chat_id=p.chat_id, chat_title=titles.get(p.chat_id) if p.chat_id else None,
            name=p.name, slug=p.slug, description=p.description, content=p.content,
            files=_clean_files(list(p.files or [])),
            rationale=p.rationale or "", tags=list(p.tags or []), source=p.source,
            created_at=p.created_at,
        )
        for p in rows
    ]


async def _unique_slug(db: AsyncSession, user: User, base: str) -> str:
    base = _slugify(base)
    existing = set(
        await db.scalars(select(Skill.slug).where(Skill.user_id == user.id))
    )
    if base not in existing:
        return base
    for n in range(2, 100):
        cand = f"{base}_{n}"[:64]
        if cand not in existing:
            return cand
    return f"{base}_{uuid.uuid4().hex[:6]}"[:64]


@router.post("/proposals/{proposal_id}/approve", response_model=SkillOut)
async def approve_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    """Aprova uma proposta → cria a Skill (slug único) e marca a proposta como aprovada."""
    p = await _owned_proposal(db, proposal_id, user)
    slug = await _unique_slug(db, user, p.slug or p.name)
    skill = Skill(
        user_id=user.id, slug=slug, name=p.name, description=p.description,
        content=p.content, files=_clean_files(list(p.files or [])),
        tags=_clean_tags(list(p.tags or [])), enabled=True,
    )
    db.add(skill)
    p.status = "approved"
    await db.commit()
    await db.refresh(skill)
    return skill


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss_proposal(
    proposal_id: uuid.UUID,
    user: User = Depends(require_approved),
    db: AsyncSession = Depends(get_db),
):
    p = await _owned_proposal(db, proposal_id, user)
    p.status = "dismissed"
    await db.commit()
    return {"ok": True}
