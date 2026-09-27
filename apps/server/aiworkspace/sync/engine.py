"""Coletar (quem envia) e aplicar (quem recebe) mudanças.

Mudança = {"t": tabela, "id": linha, "ts": quando, "o": instância de origem,
           "d": apagada?, "r": linha na forma de viagem (None se apagada)}.

Conflito: por LINHA, o mais recente vence (ts; empate → id da origem). A versão de
cada linha fica em `sync_rows`, gravada pelo gatilho — inclusive a das linhas que
chegaram de fora, com a hora e a origem originais.

Ordem: pais antes dos filhos. Mesmo assim um filho pode chegar antes do pai (o pai
foi editado depois e a entrada dele "andou" para o fim da fila): quem falha por FK
fica para uma nova tentativa no fim do lote e, se ainda faltar, em `sync_pending`.

Primeiro contato: as duas instâncias podem ter "a mesma coisa" com ids diferentes
(a chave "openrouter", o modelo de slug "gpt"). Pela chave natural (UNIQUE com
user_id), a linha local ADOTA o id de lá — as referências a ela (FKs e ids dentro
de JSON) acompanham — e aí o mais recente vence, sem duplicar nada.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from . import codec
from .tables import RAW_MEMORIES, TableInfo, order_index, synced

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Coletar                                                                     #
# --------------------------------------------------------------------------- #
BATCH_BYTES = 8 * 1024 * 1024  # por troca (o corpo das requisições tem teto de 32 MB)


async def collect(conn, *, since: int, exclude_origin: str, local_users: set[str],
                  fernet: Fernet, limit: int = 400) -> dict:
    """Mudanças desta instância depois de `since`, para a instância `exclude_origin`
    (o que veio DELA não volta), só das contas em comum. Para em ~8 MB (imagens
    geradas viajam na própria linha); sempre leva ao menos uma mudança."""
    rows = (await conn.execute(text(
        "SELECT tbl, row_id, seq, ts, origin, deleted FROM sync_rows "
        "WHERE seq > :s AND origin <> CAST(:o AS uuid) ORDER BY seq LIMIT :n"),
        {"s": since, "o": exclude_origin, "n": limit})).all()
    more = len(rows) == limit
    nxt = rows[-1].seq if rows else since
    tabelas = synced()
    por_tabela: dict[str, list] = {}
    for r in rows:
        if r.tbl in tabelas and not r.deleted:
            por_tabela.setdefault(r.tbl, []).append(r.row_id)
    dados: dict[tuple[str, str], dict] = {}
    for tbl, ids in por_tabela.items():
        res = await conn.execute(
            text(f'SELECT to_jsonb(x) FROM "{tbl}" x WHERE x.id = ANY(:ids)'), {"ids": ids})
        for (j,) in res:
            dados[(tbl, str(j["id"]))] = j
    donos = await _owners(conn, dados)
    changes: list[dict] = []
    tamanho = 0
    for r in rows:
        if changes and tamanho >= BATCH_BYTES:
            more = True
            break
        nxt = r.seq
        info = tabelas.get(r.tbl)
        if info is None:
            continue
        ch = {"t": r.tbl, "id": str(r.row_id), "ts": r.ts.isoformat(), "o": str(r.origin), "d": r.deleted}
        if not r.deleted:
            j = dados.get((r.tbl, str(r.row_id)))
            if j is None:          # apagada depois de registrada: a exclusão vem a seguir
                continue
            if donos.get((r.tbl, str(r.row_id))) not in local_users:
                continue           # conta que não é comum às duas instâncias
            ch["r"] = codec.outgoing(info, j, fernet)
            tamanho += len(json.dumps(ch["r"], default=str))
        changes.append(ch)
    return {"changes": changes, "next": nxt, "more": more}


async def _owners(conn, dados: dict[tuple[str, str], dict]) -> dict[tuple[str, str], str | None]:
    tabelas = synced()
    out: dict[tuple[str, str], str | None] = {}
    pais: dict[str, set[str]] = {}
    for (tbl, rid), j in dados.items():
        info = tabelas[tbl]
        if info.parent:
            col, ptbl = info.parent
            if j.get(col):
                pais.setdefault(ptbl, set()).add(str(j[col]))
        else:
            out[(tbl, rid)] = codec.owner_of(info, j)
    dono_pai: dict[tuple[str, str], str] = {}
    for ptbl, ids in pais.items():
        res = await conn.execute(
            text(f'SELECT id, user_id FROM "{ptbl}" WHERE id = ANY(CAST(:ids AS uuid[]))'), {"ids": list(ids)})
        for pid, uid in res:
            dono_pai[(ptbl, str(pid))] = str(uid) if uid else None
    for (tbl, rid), j in dados.items():
        info = tabelas[tbl]
        if info.parent:
            col, ptbl = info.parent
            out[(tbl, rid)] = dono_pai.get((ptbl, str(j.get(col))))
    return out


# --------------------------------------------------------------------------- #
# Aplicar                                                                     #
# --------------------------------------------------------------------------- #
def _sqlstate(exc: BaseException) -> str | None:
    for e in (getattr(exc, "orig", None), getattr(getattr(exc, "orig", None), "__cause__", None)):
        st = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
        if st:
            return str(st)
    return None


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v)


async def _local_version(conn, tbl: str, rid: str):
    return (await conn.execute(text(
        "SELECT ts, origin FROM sync_rows WHERE tbl = :t AND row_id = CAST(:i AS uuid)"),
        {"t": tbl, "i": rid})).first()


def _newer(ts: datetime, origin: str, local) -> bool:
    if local is None:
        return True
    return (ts, origin) > (local.ts, str(local.origin))


async def _mark(conn, origin: str, ts: str) -> None:
    await conn.execute(text("SELECT set_config('aiw.sync_origin', :o, true), set_config('aiw.sync_ts', :t, true)"),
                       {"o": origin, "t": ts})


async def _columns(conn, tbl: str, cache: dict[str, set[str]]) -> set[str]:
    if tbl not in cache:
        res = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = :t"),
            {"t": tbl})
        cache[tbl] = {r[0] for r in res}
    return cache[tbl]


async def _upsert(conn, tbl: str, row: dict, cols_db: set[str]) -> None:
    cols = [c for c in row if c in cols_db]
    lista = ", ".join(f'"{c}"' for c in cols)
    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "id")
    await conn.execute(text(
        f'INSERT INTO "{tbl}" ({lista}) SELECT {lista} FROM jsonb_populate_record(NULL::"{tbl}", CAST(:j AS jsonb)) '
        f'ON CONFLICT (id) DO {"UPDATE SET " + sets if sets else "NOTHING"}'),
        {"j": json.dumps(row)})


async def _rekey(conn, info: TableInfo, old: str, new: str) -> None:
    """A linha local `old` passa a se chamar `new` (o id que a outra instância usa para
    a mesma coisa). Filhas e ids dentro de JSON acompanham; a versão (hora/origem) é
    preservada, para o "mais recente vence" comparar o conteúdo e não o re-chaveamento."""
    ver = await _local_version(conn, info.name, old)
    if ver is not None:
        await _mark(conn, str(ver.origin), ver.ts.isoformat())
    naturais = sorted({c for nk in info.natural_keys for c in nk if c != "user_id"})
    orig = (await conn.execute(text(
        f'SELECT {", ".join(chr(34) + c + chr(34) for c in naturais)} FROM "{info.name}" WHERE id = CAST(:o AS uuid)'),
        {"o": old})).first()
    # 1) a original libera a chave natural, 2) a cópia nasce com o id novo e a chave
    # verdadeira, 3) as filhas passam para a cópia, 4) ids dentro de JSON, 5) some a original
    await conn.execute(text(
        f'UPDATE "{info.name}" SET ' + ", ".join(f'"{c}" = "{c}" || :suf' for c in naturais)
        + " WHERE id = CAST(:o AS uuid)"), {"o": old, "suf": f"~aiw-rekey-{old[:8]}"})
    extra = {"id": new, **{c: (orig[i] if orig is not None else None) for i, c in enumerate(naturais)}}
    await conn.execute(text(
        f'INSERT INTO "{info.name}" SELECT (jsonb_populate_record(NULL::"{info.name}", '
        f'to_jsonb(x) || CAST(:extra AS jsonb))).* FROM "{info.name}" x WHERE x.id = CAST(:o AS uuid)'),
        {"o": old, "extra": json.dumps(extra, default=str)})
    for child, col in info.fk_refs:
        await conn.execute(text(
            f'UPDATE "{child}" SET "{col}" = CAST(:n AS uuid) WHERE "{col}" = CAST(:o AS uuid)'), {"o": old, "n": new})
    for tbl, tinfo in synced().items():
        for c in tinfo.json_cols:
            await conn.execute(text(
                f'UPDATE "{tbl}" SET "{c}" = CAST(replace(CAST("{c}" AS text), :o, :n) AS jsonb) '
                f'WHERE CAST("{c}" AS text) LIKE :pat'), {"o": old, "n": new, "pat": f"%{old}%"})
    await conn.execute(text(f'DELETE FROM "{info.name}" WHERE id = CAST(:o AS uuid)'), {"o": old})


async def _natural_match(conn, info: TableInfo, row: dict) -> str | None:
    """Id local da "mesma coisa" (mesma chave natural) com outro id."""
    for nk in info.natural_keys:
        if any(row.get(c) is None for c in nk):
            continue
        cond = " AND ".join(f'CAST("{c}" AS text) = :v{i}' for i, c in enumerate(nk))
        hit = (await conn.execute(text(
            f'SELECT id FROM "{info.name}" WHERE {cond} AND id <> CAST(:id AS uuid) LIMIT 1'),
            {"id": row["id"], **{f"v{i}": str(row[c]) for i, c in enumerate(nk)}})).scalar()
        if hit:
            return str(hit)
    return None


async def _apply_one(conn, ch: dict, *, fernet: Fernet, user_map: dict[str, str],
                     cols_cache: dict[str, set[str]]) -> str:
    info = synced().get(ch["t"])
    if info is None:
        return "skipped"            # tabela que esta versão não conhece
    ts = _ts(ch["ts"])
    local = await _local_version(conn, ch["t"], ch["id"])
    if not _newer(ts, ch["o"], local):
        return "skipped"
    row = None
    if not ch.get("d"):
        row = codec.incoming(info, ch.get("r") or {}, fernet, user_map)
        if row is None:
            return "skipped"        # conta que não existe aqui
    try:
        async with conn.begin_nested():
            if ch.get("d"):
                await _mark(conn, ch["o"], ch["ts"])
                res = await conn.execute(text(f'DELETE FROM "{ch["t"]}" WHERE id = CAST(:i AS uuid)'), {"i": ch["id"]})
                if not res.rowcount:
                    # nada a apagar aqui: guarda a lápide mesmo assim (uma edição antiga
                    # que chegue depois não ressuscita a linha)
                    await conn.execute(text(
                        "INSERT INTO sync_rows (tbl, row_id, seq, ts, origin, deleted) "
                        "VALUES (:t, CAST(:i AS uuid), nextval('sync_seq'), CAST(:ts AS timestamptz), CAST(:o AS uuid), true) "
                        "ON CONFLICT (tbl, row_id) DO UPDATE SET seq = EXCLUDED.seq, ts = EXCLUDED.ts, "
                        "origin = EXCLUDED.origin, deleted = true"),
                        {"t": ch["t"], "i": ch["id"], "ts": ch["ts"], "o": ch["o"]})
                return "applied"
            if local is None and info.natural_keys:
                twin = await _natural_match(conn, info, row)
                if twin:
                    await _rekey(conn, info, twin, ch["id"])
                    local = await _local_version(conn, ch["t"], ch["id"])
                    if not _newer(ts, ch["o"], local):
                        return "applied"   # ficou a versão local (mais recente), já com o id comum
            await _mark(conn, ch["o"], ch["ts"])
            await _upsert(conn, ch["t"], row, await _columns(conn, ch["t"], cols_cache))
        return "applied"
    except DBAPIError as exc:
        st = _sqlstate(exc)
        if st == "23503":
            return "fk"             # o pai ainda não chegou
        if st == "23505":
            logger.warning("sync: conflito de unicidade em %s/%s: %s", ch["t"], ch["id"], str(exc)[:300])
            return "conflict"
        logger.warning("sync: falha ao aplicar %s/%s: %s", ch["t"], ch["id"], str(exc)[:300])
        return "error"


async def apply(conn, changes: list[dict], *, fernet: Fernet, user_map: dict[str, str]) -> dict:
    """Aplica um lote. Devolve contagens, as mudanças que ficaram esperando o pai
    (`pending`) e os anexos que chegaram (p/ buscar os arquivos)."""
    ordem = order_index()
    fim = len(ordem) + 1
    ups = [c for c in changes if not c.get("d")]
    dels = [c for c in changes if c.get("d")]
    ups.sort(key=lambda c: ordem.get(c["t"], fim))           # pais primeiro
    dels.sort(key=lambda c: -ordem.get(c["t"], fim))         # filhos primeiro
    stats = {"applied": 0, "skipped": 0, "conflicts": 0, "errors": 0}
    uploads: list[str] = []
    cache: dict[str, set[str]] = {}
    fila = ups + dels
    while fila:
        espera: list[dict] = []
        for ch in fila:
            r = await _apply_one(conn, ch, fernet=fernet, user_map=user_map, cols_cache=cache)
            if r == "fk":
                espera.append(ch)
            elif r == "applied":
                stats["applied"] += 1
                if ch["t"] == "uploads" and not ch.get("d"):
                    uploads.append(ch["id"])
            elif r == "conflict":
                stats["conflicts"] += 1
            elif r == "error":
                stats["errors"] += 1
            else:
                stats["skipped"] += 1
        if len(espera) == len(fila):
            break                    # ninguém andou: o pai não está neste lote
        fila = espera
    else:
        espera = []
    return {**stats, "pending": espera, "uploads": uploads}


def local_users(user_map: dict[str, str]) -> set[str]:
    return {str(v) for v in (user_map or {}).values()}


def build_user_map(remote_users: list[dict[str, Any]], local: list[dict[str, Any]]) -> dict[str, str]:
    """{id lá: id cá} das contas com o mesmo e-mail."""
    por_email = {str(u.get("email") or "").strip().lower(): str(u["id"]) for u in local if u.get("email")}
    out: dict[str, str] = {}
    for u in remote_users or []:
        e = str(u.get("email") or "").strip().lower()
        if e and e in por_email:
            out[str(u["id"])] = por_email[e]
    return out


__all__ = ["collect", "apply", "build_user_map", "local_users", "RAW_MEMORIES"]
