"""Captura de mudanças: um gatilho por tabela replicada grava em `sync_rows` a versão
de cada linha (quando mudou, de qual instância veio, se foi apagada).

O gatilho pega TODA escrita — ORM, SQL cru, cascata de exclusão — sem depender de a
aplicação lembrar de marcar nada. Quem aplica mudanças vindas de outra instância liga
`aiw.sync_origin`/`aiw.sync_ts` na transação: a linha fica com a origem e a hora
ORIGINAIS (é assim que a mudança não volta para quem a mandou, e o "mais recente
vence" compara as horas certas).

Só é instalado quando a primeira instância é pareada: quem não usa sync não paga nada.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from .tables import synced

INSTANCE_KEY = "sync:instance"

_FUNCTION = """
CREATE OR REPLACE FUNCTION aiw_sync_capture() RETURNS trigger AS $$
DECLARE
  rid uuid;
  org text := current_setting('aiw.sync_origin', true);
  tsv text := current_setting('aiw.sync_ts', true);
BEGIN
  IF TG_OP = 'DELETE' THEN rid := OLD.id; ELSE rid := NEW.id; END IF;
  INSERT INTO sync_rows (tbl, row_id, seq, ts, origin, deleted)
  VALUES (
    TG_TABLE_NAME, rid, nextval('sync_seq'),
    COALESCE(NULLIF(tsv, '')::timestamptz, clock_timestamp()),
    COALESCE(NULLIF(org, '')::uuid,
             (SELECT (value->'v'->>'id')::uuid FROM app_settings WHERE key = 'sync:instance')),
    TG_OP = 'DELETE')
  ON CONFLICT (tbl, row_id) DO UPDATE
    SET seq = EXCLUDED.seq, ts = EXCLUDED.ts, origin = EXCLUDED.origin, deleted = EXCLUDED.deleted;
  RETURN NULL;
END
$$ LANGUAGE plpgsql
"""


async def instance(conn) -> dict:
    """{id, name} desta instância (criado na 1ª vez)."""
    row = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = :k"), {"k": INSTANCE_KEY})).scalar()
    inst = (row or {}).get("v") if isinstance(row, dict) else None
    if inst and inst.get("id"):
        return inst
    import json
    import socket

    inst = {"id": str(uuid.uuid4()), "name": (socket.gethostname() or "AI Workspace")[:120]}
    await conn.execute(text(
        "INSERT INTO app_settings (id, key, value, created_at, updated_at) "
        "VALUES (:id, :k, CAST(:v AS jsonb), now(), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
        {"id": str(uuid.uuid4()), "k": INSTANCE_KEY, "v": json.dumps({"v": inst})})
    return inst


async def set_instance_name(conn, name: str) -> dict:
    import json

    inst = await instance(conn)
    inst = {**inst, "name": name.strip()[:120] or inst["name"]}
    await conn.execute(text("UPDATE app_settings SET value = CAST(:v AS jsonb) WHERE key = :k"),
                       {"k": INSTANCE_KEY, "v": json.dumps({"v": inst})})
    return inst


async def is_enabled(conn) -> bool:
    return bool((await conn.execute(text(
        "SELECT 1 FROM pg_trigger WHERE tgname = 'aiw_sync' LIMIT 1"))).scalar())


async def _existing_tables(conn) -> set[str]:
    rows = await conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    return {r[0] for r in rows}


async def enable(conn) -> int:
    """Instala a função + os gatilhos e registra as linhas que já existem (para a 1ª
    troca levar tudo). Idempotente: rodar de novo só completa o que faltar (ex.: uma
    tabela nova que surgiu numa atualização). Devolve quantas linhas foram registradas."""
    inst = await instance(conn)
    await conn.execute(text(_FUNCTION))
    existentes = await _existing_tables(conn)
    registradas = 0
    for tbl in synced():
        if tbl not in existentes:
            continue
        tem = (await conn.execute(text(
            "SELECT 1 FROM pg_trigger WHERE tgname = 'aiw_sync' AND tgrelid = CAST(:t AS regclass)"),
            {"t": tbl})).scalar()
        if not tem:
            await conn.execute(text(
                f'CREATE TRIGGER aiw_sync AFTER INSERT OR UPDATE OR DELETE ON "{tbl}" '
                "FOR EACH ROW EXECUTE FUNCTION aiw_sync_capture()"))
        # linhas anteriores aos gatilhos: entram com a hora da última alteração que o
        # próprio registro guarda (ou agora), na ordem de dependência
        cols = synced()[tbl].columns
        quando = ("COALESCE(updated_at, created_at, now())" if "updated_at" in cols
                  else "COALESCE(created_at, now())" if "created_at" in cols else "now()")
        res = await conn.execute(text(
            f"INSERT INTO sync_rows (tbl, row_id, seq, ts, origin, deleted) "
            f"SELECT :t, id, nextval('sync_seq'), {quando}, CAST(:o AS uuid), false FROM \"{tbl}\" "
            "ON CONFLICT (tbl, row_id) DO NOTHING"), {"t": tbl, "o": inst["id"]})
        registradas += res.rowcount or 0
    return registradas


async def disable(conn) -> None:
    """Remove os gatilhos (sem nenhuma instância pareada não há o que registrar)."""
    for tbl in await _existing_tables(conn):
        if tbl in synced():
            await conn.execute(text(f'DROP TRIGGER IF EXISTS aiw_sync ON "{tbl}"'))
    await conn.execute(text("TRUNCATE sync_rows"))
