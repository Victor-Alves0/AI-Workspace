"""Armazenamento dos anexos do chat: disco + referência no banco.

Regra de ouro deste módulo: **o arquivo nunca é lido inteiro na memória**. Ele é
gravado em pedaços enquanto chega e, na hora do turno, só é lido o que precisa ir ao
modelo (texto extraído, ou os bytes de uma imagem dentro do teto dela).
"""

from __future__ import annotations

import logging
import mimetypes
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import Upload

logger = logging.getLogger(__name__)

_IMAGE_MIME = ("image/",)
_AUDIO_MIME = ("audio/", "video/webm")


def kind_for(filename: str, mime: str) -> str:
    """image | audio | file — decide como o turno vai usar o arquivo."""
    mime = (mime or "").lower()
    if mime.startswith(_IMAGE_MIME):
        return "image"
    if any(mime.startswith(prefix) for prefix in _AUDIO_MIME):
        return "audio"
    guess = (mimetypes.guess_type(filename or "")[0] or "").lower()
    if guess.startswith("image/"):
        return "image"
    if guess.startswith("audio/"):
        return "audio"
    return "file"


def max_bytes_for(kind: str) -> int:
    """Teto por tipo. Imagem e áudio são menores porque viajam INTEIROS na requisição
    ao provedor; um documento vira texto aqui e só o texto segue."""
    s = get_settings()
    if kind == "image":
        return s.upload_image_max_bytes
    if kind == "audio":
        return s.upload_audio_max_bytes
    return s.upload_max_bytes


def root() -> Path:
    return Path(get_settings().uploads_dir)


def file_path(row: Upload) -> Path:
    return root() / row.path


def sign_url(upload_id: str, ttl_days: int = 3650) -> str:
    """`/uploads/<id>?t=<sig>` — mesma URL-capacidade da mídia gerada: o token só
    codifica o id, então serve em <img>/<audio> sem depender de cookie."""
    now = int(time.time())
    token = jwt.encode(
        {"upl": upload_id, "iat": now, "exp": now + ttl_days * 86400},
        get_settings().app_secret,
        algorithm="HS256",
    )
    return f"/uploads/{upload_id}?t={token}"


def verify_token(upload_id: str, token: str) -> bool:
    try:
        data = jwt.decode(token, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return data.get("upl") == upload_id


def sign_shared_url(upload_id: str, public_id: str, ttl_days: int = 3650) -> str:
    """URL de anexo válida apenas para o link público atual do chat."""
    now = int(time.time())
    token = jwt.encode(
        {"upl": upload_id, "share": public_id, "iat": now, "exp": now + ttl_days * 86400},
        get_settings().app_secret,
        algorithm="HS256",
    )
    return f"/uploads/{upload_id}?s={token}"


def verify_shared_token(upload_id: str, token: str) -> str | None:
    try:
        data = jwt.decode(token, get_settings().app_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    share = data.get("share")
    return str(share) if data.get("upl") == upload_id and isinstance(share, str) and share else None


def out(row: Upload) -> dict[str, Any]:
    """Contrato devolvido ao compositor e guardado no anexo da mensagem."""
    return {
        "id": str(row.id),
        "name": row.filename,
        "mime": row.mime,
        "size": row.size,
        "kind": row.kind,
        "url": sign_url(str(row.id)),
    }


async def used_bytes(db: AsyncSession, user_id: uuid.UUID) -> int:
    total = await db.scalar(select(func.sum(Upload.size)).where(Upload.user_id == user_id))
    return int(total or 0)


async def store_stream(
    db: AsyncSession, user_id: uuid.UUID, filename: str, mime: str, chunks,
) -> Upload:
    """Grava o arquivo em pedaços e devolve a linha do banco.

    `chunks` é um iterável ASSÍNCRONO de bytes (o `UploadFile` do Starlette). Estourar
    o teto aborta no meio: o que já foi para o disco é apagado, em vez de deixar meio
    arquivo ocupando espaço."""
    settings = get_settings()
    filename = (filename or "arquivo")[:255]
    mime = (mime or mimetypes.guess_type(filename)[0] or "application/octet-stream")[:128]
    kind = kind_for(filename, mime)
    cap = max_bytes_for(kind)

    upload_id = uuid.uuid4()
    relative = f"{user_id}/{upload_id}"
    destination = root() / relative
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("uploads: não deu para criar %s", destination.parent)
        raise UploadStorageError(destination, exc) from exc

    size = 0
    try:
        with destination.open("wb") as fh:
            async for chunk in chunks:
                size += len(chunk)
                if size > cap:
                    raise UploadTooLarge(kind, cap)
                fh.write(chunk)
        if not size:
            raise UploadEmpty()
        livre = settings.upload_quota_bytes - await used_bytes(db, user_id)
        if size > livre:
            raise UploadQuotaExceeded(settings.upload_quota_bytes)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        logger.exception("uploads: falha ao gravar %s", destination)
        raise UploadStorageError(destination, exc) from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    row = Upload(
        id=upload_id, user_id=user_id, filename=filename, mime=mime,
        size=size, kind=kind, path=relative,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def persistable(prepared: list[dict]) -> list[dict]:
    """O que fica GRAVADO na mensagem.

    O anexo preparado para o turno carrega a imagem embutida (é assim que ela chega ao
    provedor). Gravar isso devolveria o base64 para dentro da linha da mensagem — o
    problema que este módulo existe para resolver. Com referência, a linha fica com
    algumas centenas de bytes e a interface busca o arquivo pela URL assinada."""
    out: list[dict] = []
    for item in prepared:
        upload_id = item.get("upload_id")
        if not upload_id:
            out.append(item)
            continue
        out.append({
            "type": item.get("type") or "file",
            "name": item.get("name") or "",
            "upload_id": str(upload_id),
            "url": sign_url(str(upload_id)),
        })
    return out


async def bind(
    db: AsyncSession, user_id: uuid.UUID, chat_id: uuid.UUID, prepared: list[dict],
) -> None:
    """Marca como ANEXADO o que virou mensagem (e prende ao chat, p/ sumir junto com
    ele). Sem isto a faxina de órfãos apagaria o arquivo de uma conversa em uso."""
    ids: list[uuid.UUID] = []
    for item in prepared:
        raw = item.get("upload_id")
        if not raw:
            continue
        try:
            ids.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    if not ids:
        return
    rows = await db.scalars(
        select(Upload).where(Upload.id.in_(ids), Upload.user_id == user_id)
    )
    for row in rows:
        row.attached = True
        if row.chat_id is None:
            row.chat_id = chat_id
    await db.commit()


def read_bytes(row: Upload, limit: int | None = None) -> bytes | None:
    """Bytes do arquivo — só para o que precisa ir inteiro ao provedor (imagem/áudio).
    `limit` recusa em vez de carregar um arquivo maior do que o chamador aguenta."""
    path = file_path(row)
    try:
        if limit is not None and path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        logger.warning("upload %s sumiu do disco (%s)", row.id, path)
        return None


def delete_file(row: Upload) -> None:
    try:
        file_path(row).unlink(missing_ok=True)
    except OSError:  # noqa: BLE001 - some do banco de qualquer forma
        logger.warning("não foi possível apagar o arquivo do upload %s", row.id)


async def purge_orphans(db: AsyncSession) -> int:
    """Apaga o que foi enviado e nunca virou mensagem (o usuário desistiu do envio).
    Sem isto, cada anexo abandonado fica no disco para sempre."""
    ttl = get_settings().upload_orphan_ttl_hours
    if ttl <= 0:
        return 0
    corte = datetime.now(timezone.utc) - timedelta(hours=ttl)
    rows = list(await db.scalars(
        select(Upload).where(Upload.attached.is_(False), Upload.created_at < corte)
    ))
    for row in rows:
        delete_file(row)
        await db.delete(row)
    if rows:
        await db.commit()
        logger.info("uploads órfãos apagados: %d", len(rows))
    return len(rows)


class UploadError(Exception):
    """Erro de envio com mensagem pronta para o usuário."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class UploadTooLarge(UploadError):
    status_code = 413

    def __init__(self, kind: str, cap: int):
        rotulo = {"image": "Imagem", "audio": "Áudio"}.get(kind, "Arquivo")
        super().__init__(f"{rotulo} acima do limite de {cap // (1024 * 1024)} MB.")


class UploadEmpty(UploadError):
    def __init__(self):
        super().__init__("Arquivo vazio.")


class UploadStorageError(UploadError):
    """O disco recusou (pasta sem permissão, volume não montado, sem espaço). Vira
    mensagem explicando ONDE olhar — antes virava 500 seco e, sem CORS na resposta, o
    navegador só dizia "bloqueado por CORS"."""

    status_code = 507

    def __init__(self, destino: Path, exc: OSError):
        super().__init__(
            f"O servidor não conseguiu gravar o anexo em {destino.parent} ({exc.strerror or exc}). "
            "Verifique o volume de uploads (permissão de escrita e espaço em disco)."
        )


class UploadQuotaExceeded(UploadError):
    status_code = 413

    def __init__(self, quota: int):
        super().__init__(
            f"Seus anexos já ocupam o limite de {quota // (1024 ** 3)} GB. "
            "Apague arquivos antigos para enviar mais."
        )


def ensure_root() -> None:
    """Cria a pasta no boot: falhar aqui é melhor do que no primeiro envio."""
    try:
        root().mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("não foi possível criar %s (%s)", root(), exc)
