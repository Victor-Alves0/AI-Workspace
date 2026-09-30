"""Pareamento, troca e agendamento da sincronização.

Topologia: quem ALCANÇA a outra instância inicia (o desktop alcança o servidor; o
servidor, atrás do NAT do desktop, não precisa alcançá-lo). Cada troca é uma ida só:
leva as mudanças daqui (push) e traz as de lá (pull), repetida até esvaziar.

Segurança: o pareamento usa um código de uso único gerado pelo admin da outra
instância (10 min, 5 tentativas). Depois, todo corpo de troca vai cifrado e
autenticado com o segredo do par (Fernet, com validade de 10 min contra replay).
Arquivos de anexo trafegam com um token do mesmo segredo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from .. import crypto
from . import capture, engine

logger = logging.getLogger(__name__)

PROTOCOL = 1
CODE_KEY = "sync:pair_code"
CODE_TTL = 600
INTERVAL = 60.0
_TTL = 600
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_locks: dict[str, asyncio.Lock] = {}


class SyncError(Exception):
    """Falha com mensagem para o admin (vai para a UI)."""


# --------------------------------------------------------------------------- #
# utilidades                                                                  #
# --------------------------------------------------------------------------- #
def _session_factory():
    from ..db import SessionLocal
    return SessionLocal


def _db_engine():
    from ..db import engine as _engine
    return _engine


def pair_fernet(secret: str) -> Fernet:
    return Fernet(secret.encode("ascii"))


def seal(secret: str, payload: dict) -> bytes:
    return pair_fernet(secret).encrypt(json.dumps(payload, default=str).encode("utf-8"))


def unseal(secret: str, body: bytes) -> dict:
    try:
        return json.loads(pair_fernet(secret).decrypt(body, ttl=_TTL))
    except (InvalidToken, ValueError) as exc:
        raise SyncError("mensagem de sincronização inválida ou expirada") from exc


def blob_token(secret: str, method: str, upload_id: str) -> str:
    return pair_fernet(secret).encrypt(f"blob:{method}:{upload_id}".encode()).decode()


def check_blob_token(secret: str, token: str, method: str, upload_id: str) -> bool:
    try:
        return pair_fernet(secret).decrypt(token.encode(), ttl=_TTL).decode() == f"blob:{method}:{upload_id}"
    except (InvalidToken, ValueError):
        return False


def normalize_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u and not u.lower().startswith(("http://", "https://")):
        u = "http://" + u
    return u


async def local_users(conn) -> list[dict]:
    rows = await conn.execute(text("SELECT id, email FROM users WHERE email IS NOT NULL"))
    return [{"id": str(i), "email": e} for i, e in rows]


async def schema_rev(conn) -> str | None:
    try:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# pareamento                                                                  #
# --------------------------------------------------------------------------- #
async def new_code(conn) -> dict:
    code = "".join(secrets.choice(_ALFABETO) for _ in range(10))
    exp = int(time.time()) + CODE_TTL
    val = {"v": {"hash": hashlib.sha256(code.encode()).hexdigest(), "exp": exp, "tries": 0}}
    await conn.execute(text(
        "INSERT INTO app_settings (id, key, value, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :k, CAST(:v AS jsonb), now(), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": CODE_KEY, "v": json.dumps(val)})
    return {"code": f"{code[:5]}-{code[5:]}", "expires_at": exp}


async def _consume_code(conn, code: str) -> bool:
    row = (await conn.execute(text("SELECT value FROM app_settings WHERE key = :k FOR UPDATE"),
                              {"k": CODE_KEY})).scalar()
    cur = (row or {}).get("v") if isinstance(row, dict) else None
    if not cur or cur.get("exp", 0) < time.time() or cur.get("tries", 0) >= 5:
        return False
    limpo = code.replace("-", "").replace(" ", "").strip().upper()
    ok = secrets.compare_digest(hashlib.sha256(limpo.encode()).hexdigest(), cur.get("hash", ""))
    if ok:
        await conn.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": CODE_KEY})
    else:
        cur["tries"] = cur.get("tries", 0) + 1
        await conn.execute(text("UPDATE app_settings SET value = CAST(:v AS jsonb) WHERE key = :k"),
                           {"k": CODE_KEY, "v": json.dumps({"v": cur})})
    return ok


async def accept_pair(body: dict) -> dict:
    """Lado que GEROU o código: recebe o pedido da outra instância."""
    remote = body.get("instance") or {}
    rid = str(remote.get("id") or "")
    if not rid:
        raise SyncError("pedido de pareamento sem identificação da instância")
    async with _db_engine().begin() as conn:
        if not await _consume_code(conn, str(body.get("code") or "")):
            raise SyncError("código inválido ou expirado — gere outro na instância de destino")
        inst = await capture.instance(conn)
        if rid == inst["id"]:
            raise SyncError("não dá para parear a instância com ela mesma")
        users = await local_users(conn)
        user_map = engine.build_user_map(body.get("users") or [], users)
        if not user_map:
            raise SyncError("nenhuma conta em comum — as duas instâncias precisam de uma conta com o mesmo e-mail")
        secret = Fernet.generate_key().decode()
        await _save_peer(conn, rid, name=str(remote.get("name") or "")[:120], url=None, secret=secret,
                         active=False, user_map=user_map, schema=remote.get("schema"))
        await capture.enable(conn)
        return {"protocol": PROTOCOL, "instance": {**inst, "schema": await schema_rev(conn)},
                "users": users, "secret": secret}


async def _save_peer(conn, rid: str, *, name: str, url: str | None, secret: str, active: bool,
                     user_map: dict, schema: str | None) -> None:
    await conn.execute(text(
        "INSERT INTO sync_peers (id, name, url, secret, active, user_map, remote_schema, created_at, updated_at) "
        "VALUES (CAST(:id AS uuid), :n, :u, :s, :a, CAST(:m AS jsonb), :sc, now(), now()) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, url = EXCLUDED.url, secret = EXCLUDED.secret, "
        "active = EXCLUDED.active, user_map = EXCLUDED.user_map, remote_schema = EXCLUDED.remote_schema, "
        "push_cursor = 0, pull_cursor = 0, last_error = NULL, updated_at = now()"),
        {"id": rid, "n": name, "u": url, "s": crypto.encrypt_field(secret), "a": active,
         "m": json.dumps(user_map), "sc": schema})


async def pair_with(url: str, code: str, client: httpx.AsyncClient | None = None) -> dict:
    """Lado que DIGITOU o código: pede o pareamento à instância em `url`."""
    base = normalize_url(url)
    if not base:
        raise SyncError("informe o endereço da outra instância")
    async with _db_engine().begin() as conn:
        inst = await capture.instance(conn)
        users = await local_users(conn)
        schema = await schema_rev(conn)
    req = {"protocol": PROTOCOL, "code": code, "instance": {**inst, "schema": schema}, "users": users}
    c = client or httpx.AsyncClient(timeout=30)
    try:
        r = await c.post(f"{base}/sync/v1/pair", json=req)
    except httpx.HTTPError as exc:
        raise SyncError(f"não consegui falar com {base}: {exc}") from exc
    finally:
        if client is None:
            await c.aclose()
    if r.status_code == 404:
        raise SyncError("esse endereço não é a API de um AI Workspace atualizado")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise SyncError(str(data.get("detail") or f"a outra instância recusou (HTTP {r.status_code})"))
    remote = data.get("instance") or {}
    async with _db_engine().begin() as conn:
        user_map = engine.build_user_map(data.get("users") or [], users)
        await _save_peer(conn, str(remote["id"]), name=str(remote.get("name") or "")[:120], url=base,
                         secret=str(data["secret"]), active=True, user_map=user_map, schema=remote.get("schema"))
        await capture.enable(conn)
    return {"id": str(remote["id"]), "name": remote.get("name")}


# --------------------------------------------------------------------------- #
# troca                                                                       #
# --------------------------------------------------------------------------- #
async def _pending(conn, peer_id: str) -> list[dict]:
    rows = await conn.execute(text("SELECT change FROM sync_pending WHERE peer_id = CAST(:p AS uuid) ORDER BY id"),
                              {"p": peer_id})
    return [r[0] for r in rows]


async def _set_pending(conn, peer_id: str, changes: list[dict]) -> None:
    await conn.execute(text("DELETE FROM sync_pending WHERE peer_id = CAST(:p AS uuid)"), {"p": peer_id})
    for ch in changes:
        await conn.execute(text("INSERT INTO sync_pending (peer_id, change) VALUES (CAST(:p AS uuid), CAST(:c AS jsonb))"),
                           {"p": peer_id, "c": json.dumps(ch)})


async def apply_incoming(conn, peer_id: str, changes: list[dict], user_map: dict) -> dict:
    """Aplica o que veio do par (mais o que tinha ficado esperando o pai)."""
    lote = await _pending(conn, peer_id) + list(changes)
    res = await engine.apply(conn, lote, fernet=crypto._fernet(), user_map=user_map)
    await _set_pending(conn, peer_id, res["pending"])
    missing = []
    if res["uploads"]:
        from .. import uploads_service
        rows = await conn.execute(text("SELECT id, path FROM uploads WHERE id = ANY(CAST(:ids AS uuid[]))"),
                                  {"ids": res["uploads"]})
        for uid, path in rows:
            if path and not (uploads_service.root() / path).exists():
                missing.append(str(uid))
    return {**{k: v for k, v in res.items() if k not in ("pending", "uploads")},
            "pending": len(res["pending"]), "missing_blobs": missing}


async def _accumulate(conn, peer_id: str, novo: dict) -> dict:
    """Totais desde o pareamento (o painel mostra o acumulado, não só a última troca)."""
    atual = (await conn.execute(text("SELECT stats FROM sync_peers WHERE id = CAST(:i AS uuid)"),
                                {"i": peer_id})).scalar() or {}
    out = dict(atual)
    for k in ("sent", "received", "conflicts", "blobs"):
        out[k] = int(atual.get(k, 0)) + int(novo.get(k, 0))
    out["pending"] = int(novo.get("pending", 0))
    return out


async def handle_exchange(peer_id: str, body: bytes) -> bytes:
    """Lado passivo: aplica o push, responde com o pull. Tudo numa transação."""
    async with _db_engine().begin() as conn:
        row = (await conn.execute(text("SELECT secret, user_map FROM sync_peers WHERE id = CAST(:i AS uuid)"),
                                  {"i": peer_id})).first()
        if row is None:
            raise SyncError("instância não pareada")
        secret = crypto.decrypt_field(row.secret)
        req = unseal(secret, body)
        users = await local_users(conn)
        user_map = engine.build_user_map(req.get("users") or [], users) or dict(row.user_map or {})
        inst = await capture.instance(conn)
        pushed = await apply_incoming(conn, peer_id, (req.get("push") or {}).get("changes") or [], user_map)
        pull = await engine.collect(conn, since=int((req.get("pull") or {}).get("since") or 0),
                                    exclude_origin=peer_id, local_users=engine.local_users(user_map),
                                    fernet=crypto._fernet())
        await conn.execute(text(
            "UPDATE sync_peers SET user_map = CAST(:m AS jsonb), last_sync_at = now(), last_error = NULL, "
            "remote_schema = :sc, stats = CAST(:st AS jsonb), updated_at = now() WHERE id = CAST(:i AS uuid)"),
            {"m": json.dumps(user_map), "sc": req.get("schema"), "i": peer_id,
             "st": json.dumps(await _accumulate(conn, peer_id, {
                 "received": pushed.get("applied", 0), "conflicts": pushed.get("conflicts", 0),
                 "pending": pushed.get("pending", 0)}))})
        resp = {"protocol": PROTOCOL, "instance": inst, "schema": await schema_rev(conn), "users": users,
                "pushed": pushed, "pull": pull}
    return seal(secret, resp)


async def run_cycle(peer_id: str, client: httpx.AsyncClient | None = None, *, wait: bool = False) -> dict:
    """Lado ativo: troca com o par até não sobrar nada dos dois lados. Com uma troca já
    em andamento: o agendador pula (`busy`); o botão "Sincronizar agora" espera a vez."""
    lock = _locks.setdefault(peer_id, asyncio.Lock())
    if lock.locked() and not wait:
        return {"busy": True}
    async with lock:
        try:
            from .. import tracing

            # ciclo do agendador (sem request): trace próprio; pelo botão, entra no da request
            with tracing.start_trace("sync:cycle", kind="worker") as _tr:
                _tr.set(peer=peer_id)
                return await _cycle(peer_id, client)
        except Exception as exc:  # noqa: BLE001 - vai para a UI
            msg = str(exc) if isinstance(exc, SyncError) else f"{type(exc).__name__}: {exc}"
            async with _db_engine().begin() as conn:
                await conn.execute(text("UPDATE sync_peers SET last_error = :e, updated_at = now() WHERE id = CAST(:i AS uuid)"),
                                   {"e": msg[:500], "i": peer_id})
            logger.warning("sync com %s falhou: %s", peer_id, msg)
            raise


async def _cycle(peer_id: str, client: httpx.AsyncClient | None) -> dict:
    total = {"sent": 0, "received": 0, "conflicts": 0, "pending": 0, "blobs": 0}
    own_client = client is None
    c = client or httpx.AsyncClient(timeout=120)
    try:
        for _ in range(500):
            async with _db_engine().begin() as conn:
                peer = (await conn.execute(text(
                    "SELECT id, url, secret, user_map, push_cursor, pull_cursor, active FROM sync_peers "
                    "WHERE id = CAST(:i AS uuid)"), {"i": peer_id})).first()
                if peer is None or not peer.active or not peer.url:
                    raise SyncError("esta instância não inicia a troca com esse par")
                secret = crypto.decrypt_field(peer.secret)
                inst = await capture.instance(conn)
                users = await local_users(conn)
                user_map = dict(peer.user_map or {})
                push = await engine.collect(conn, since=peer.push_cursor, exclude_origin=peer_id,
                                            local_users=engine.local_users(user_map), fernet=crypto._fernet())
                schema = await schema_rev(conn)
            req = {"protocol": PROTOCOL, "users": users, "schema": schema,
                   "push": {"changes": push["changes"]}, "pull": {"since": peer.pull_cursor}}
            try:
                r = await c.post(f"{peer.url}/sync/v1/exchange", content=seal(secret, req),
                                 headers={"X-AIW-Peer": inst["id"], "Content-Type": "application/octet-stream"})
            except httpx.HTTPError as exc:
                raise SyncError(f"sem conexão com {peer.url}: {exc}") from exc
            if r.status_code != 200:
                detail = ""
                try:
                    detail = r.json().get("detail", "")
                except ValueError:
                    pass
                raise SyncError(detail or f"a outra instância respondeu HTTP {r.status_code}")
            resp = unseal(secret, r.content)
            pull = resp.get("pull") or {}
            async with _db_engine().begin() as conn:
                user_map = engine.build_user_map(resp.get("users") or [], users) or user_map
                got = await apply_incoming(conn, peer_id, pull.get("changes") or [], user_map)
                await conn.execute(text(
                    "UPDATE sync_peers SET push_cursor = :pc, pull_cursor = :lc, user_map = CAST(:m AS jsonb), "
                    "remote_schema = :sc, name = COALESCE(NULLIF(:n, ''), name), last_error = NULL, updated_at = now() "
                    "WHERE id = CAST(:i AS uuid)"),
                    {"pc": push["next"], "lc": int(pull.get("next") or peer.pull_cursor), "m": json.dumps(user_map),
                     "sc": resp.get("schema"), "n": str((resp.get("instance") or {}).get("name") or ""), "i": peer_id})
            pushed = resp.get("pushed") or {}
            total["sent"] += pushed.get("applied", 0)
            total["received"] += got.get("applied", 0)
            total["conflicts"] += pushed.get("conflicts", 0) + got.get("conflicts", 0)
            total["pending"] = got.get("pending", 0)
            total["blobs"] += await _blobs(c, peer.url, secret, inst["id"],
                                           send=pushed.get("missing_blobs") or [], fetch=got.get("missing_blobs") or [])
            if not push["more"] and not pull.get("more"):
                break
        async with _db_engine().begin() as conn:
            await conn.execute(text(
                "UPDATE sync_peers SET last_sync_at = now(), stats = CAST(:st AS jsonb), updated_at = now() "
                "WHERE id = CAST(:i AS uuid)"), {"st": json.dumps(await _accumulate(conn, peer_id, total)), "i": peer_id})
        return total
    finally:
        if own_client:
            await c.aclose()


async def _blobs(c: httpx.AsyncClient, base: str, secret: str, me: str, *, send: list[str], fetch: list[str]) -> int:
    """Arquivos dos anexos: manda os que o par não tem, busca os que faltam aqui."""
    from .. import uploads_service

    feitos = 0
    async with _db_engine().begin() as conn:
        rows = await conn.execute(text("SELECT id, path FROM uploads WHERE id = ANY(CAST(:ids AS uuid[]))"),
                                  {"ids": list({*send, *fetch})})
        paths = {str(i): p for i, p in rows}
    for uid in send:
        p = paths.get(uid)
        f = uploads_service.root() / p if p else None
        if not f or not f.exists():
            continue
        try:
            r = await c.put(f"{base}/sync/v1/blob/{uid}", content=f.read_bytes(),
                            headers={"X-AIW-Peer": me, "X-AIW-Auth": blob_token(secret, "PUT", uid)})
            feitos += r.status_code == 200
        except httpx.HTTPError as exc:
            logger.warning("sync: falha ao enviar o arquivo %s: %s", uid, exc)
    for uid in fetch:
        p = paths.get(uid)
        if not p:
            continue
        try:
            r = await c.get(f"{base}/sync/v1/blob/{uid}",
                            headers={"X-AIW-Peer": me, "X-AIW-Auth": blob_token(secret, "GET", uid)})
            if r.status_code == 200:
                dest = uploads_service.root() / p
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                feitos += 1
        except httpx.HTTPError as exc:
            logger.warning("sync: falha ao baixar o arquivo %s: %s", uid, exc)
    return feitos


# --------------------------------------------------------------------------- #
# agendador                                                                   #
# --------------------------------------------------------------------------- #
_task: asyncio.Task | None = None


async def _loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL)
        try:
            async with _db_engine().begin() as conn:
                ids = [str(r[0]) for r in await conn.execute(text(
                    "SELECT id FROM sync_peers WHERE active AND url IS NOT NULL"))]
        except Exception:  # noqa: BLE001 - banco fora do ar: tenta no próximo ciclo
            continue
        for pid in ids:
            try:
                await run_cycle(pid)
            except Exception:  # noqa: BLE001 - já registrado em last_error
                pass


async def ensure_capture() -> None:
    """No boot: se há pares, garante os gatilhos (uma tabela nova de uma atualização
    passa a ser capturada, e as linhas dela entram na próxima troca)."""
    try:
        async with _db_engine().begin() as conn:
            if (await conn.execute(text("SELECT 1 FROM sync_peers LIMIT 1"))).scalar():
                await capture.enable(conn)
    except Exception as exc:  # noqa: BLE001 - banco sem a migração ainda etc.
        logger.warning("sync: não consegui conferir a captura de mudanças (%s)", exc)


def start_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


__all__ = ["SyncError", "pair_with", "accept_pair", "handle_exchange", "run_cycle", "new_code",
           "start_scheduler", "check_blob_token"]
