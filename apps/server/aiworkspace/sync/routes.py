"""Rotas da sincronização.

`/sync/v1/*` — chamadas pela OUTRA instância (sem sessão de usuário): o pareamento
se autentica pelo código de uso único; o resto, pelo segredo do par.
`/admin/sync/*` — o painel do admin (listar, gerar código, adicionar, sincronizar,
remover instâncias).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from .. import crypto
from ..auth.deps import require_admin
from ..models import User
from . import capture, service

logger = logging.getLogger(__name__)

peer_router = APIRouter(prefix="/sync/v1", tags=["sync"])
admin_router = APIRouter(prefix="/admin/sync", tags=["sync"])


# --------------------------------------------------------------------------- #
# chamadas da outra instância                                                 #
# --------------------------------------------------------------------------- #
@peer_router.post("/pair")
async def pair(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pedido inválido") from None
    if int(body.get("protocol") or 0) != service.PROTOCOL:
        raise HTTPException(status.HTTP_409_CONFLICT, "versões de sincronização diferentes — atualize as duas instâncias")
    try:
        return await service.accept_pair(body)
    except service.SyncError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None


@peer_router.post("/exchange")
async def exchange(request: Request, x_aiw_peer: str = Header(default="")):
    if not _is_uuid(x_aiw_peer):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "instância não pareada")
    try:
        out = await service.handle_exchange(x_aiw_peer, await request.body())
    except service.SyncError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "instância não pareada") from None
    return Response(out, media_type="application/octet-stream")


def _is_uuid(v: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


async def _blob_row(peer_id: str, token: str, method: str, upload_id: str):
    """Confere o token do par e que o anexo é de uma conta em comum."""
    from ..db import engine

    if not (_is_uuid(peer_id) and _is_uuid(upload_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "não autorizado")

    async with engine.begin() as conn:
        peer = (await conn.execute(text("SELECT secret, user_map FROM sync_peers WHERE id = CAST(:i AS uuid)"),
                                   {"i": peer_id})).first()
        row = (await conn.execute(text("SELECT user_id, path FROM uploads WHERE id = CAST(:u AS uuid)"),
                                  {"u": upload_id})).first()
    if peer is None or not service.check_blob_token(crypto.decrypt_field(peer.secret), token, method, upload_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "não autorizado")
    if row is None or not row.path or str(row.user_id) not in {str(v) for v in (peer.user_map or {}).values()}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "anexo não encontrado")
    return row


@peer_router.get("/blob/{upload_id}")
async def get_blob(upload_id: str, x_aiw_peer: str = Header(default=""), x_aiw_auth: str = Header(default="")):
    from .. import uploads_service

    row = await _blob_row(x_aiw_peer, x_aiw_auth, "GET", upload_id)
    f = uploads_service.root() / row.path
    if not f.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "arquivo não encontrado")
    return FileResponse(f)


@peer_router.put("/blob/{upload_id}")
async def put_blob(upload_id: str, request: Request, x_aiw_peer: str = Header(default=""),
                   x_aiw_auth: str = Header(default="")):
    from .. import uploads_service

    row = await _blob_row(x_aiw_peer, x_aiw_auth, "PUT", upload_id)
    root = uploads_service.root().resolve()
    dest = (root / row.path).resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "caminho inválido") from None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as fh:
        async for chunk in request.stream():
            fh.write(chunk)
    tmp.replace(dest)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# painel do admin                                                             #
# --------------------------------------------------------------------------- #
class InstanceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PeerIn(BaseModel):
    url: str = Field(min_length=3, max_length=512)
    code: str = Field(min_length=6, max_length=32)


async def _overview(conn) -> dict:
    inst = await capture.instance(conn)
    rows = await conn.execute(text(
        "SELECT p.id, p.name, p.url, p.active, p.user_map, p.last_sync_at, p.last_error, p.stats, "
        "p.remote_schema, p.created_at, (SELECT count(*) FROM sync_pending s WHERE s.peer_id = p.id) AS pending "
        "FROM sync_peers p ORDER BY p.created_at"))
    emails = {str(i): e for i, e in await conn.execute(text("SELECT id, email FROM users"))}
    schema = await service.schema_rev(conn)
    peers = []
    for r in rows:
        peers.append({
            "id": str(r.id), "name": r.name, "url": r.url, "active": r.active,
            "accounts": sorted({emails.get(str(v), "") for v in (r.user_map or {}).values()} - {""}),
            "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
            "last_error": r.last_error, "stats": r.stats or {}, "pending": r.pending,
            "same_version": (r.remote_schema or schema) == schema,
        })
    return {"instance": inst, "enabled": await capture.is_enabled(conn), "peers": peers}


@admin_router.get("")
async def overview(_: User = Depends(require_admin)):
    from ..db import engine

    async with engine.begin() as conn:
        return await _overview(conn)


@admin_router.patch("/instance")
async def rename_instance(body: InstanceIn, _: User = Depends(require_admin)):
    from ..db import engine

    async with engine.begin() as conn:
        return await capture.set_instance_name(conn, body.name)


@admin_router.post("/code")
async def pairing_code(_: User = Depends(require_admin)):
    from ..db import engine

    async with engine.begin() as conn:
        return await service.new_code(conn)


@admin_router.post("/peers")
async def add_peer(body: PeerIn, background: BackgroundTasks, admin: User = Depends(require_admin)):
    try:
        peer = await service.pair_with(body.url, body.code)
    except service.SyncError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    from .. import audit_service
    await audit_service.record("sync_peer_added", user_id=admin.id, detail={"peer": peer["id"], "url": body.url})
    background.add_task(_quiet_cycle, peer["id"])  # 1ª troca já começa
    return peer


async def _quiet_cycle(peer_id: str) -> None:
    try:
        await service.run_cycle(peer_id)
    except Exception:  # noqa: BLE001 - fica em last_error
        pass


@admin_router.post("/peers/{peer_id}/sync")
async def sync_now(peer_id: str, _: User = Depends(require_admin)):
    try:
        res = await service.run_cycle(peer_id, wait=True)
    except service.SyncError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"falha na sincronização: {exc}") from None
    return res


@admin_router.delete("/peers/{peer_id}")
async def remove_peer(peer_id: str, admin: User = Depends(require_admin)):
    """Desfaz o pareamento (os dados que já vieram ficam). Sem nenhum par, a captura
    de mudanças é desligada."""
    from .. import audit_service
    from ..db import engine

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM sync_peers WHERE id = CAST(:i AS uuid)"), {"i": peer_id})
        if not (await conn.execute(text("SELECT 1 FROM sync_peers LIMIT 1"))).scalar():
            await capture.disable(conn)
    await audit_service.record("sync_peer_removed", user_id=admin.id, detail={"peer": peer_id})
    return {"ok": True}
