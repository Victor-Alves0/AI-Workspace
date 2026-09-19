"""Bateria de banco: Postgres DE VERDADE, um banco descartável por teste.

Só roda com `TEST_DATABASE_URL` apontando para um servidor onde dá para criar e
dropar bancos (ex.: `postgresql://postgres@localhost:5432/postgres`, com pgvector).
Sem ela, tudo aqui é pulado — o `pytest` de sempre continua hermético. A CI sobe um
`pgvector/pgvector:pg16` e roda com ela (.github/workflows/tests.yml).

O banco apontado NUNCA é tocado: cada teste cria `aiw_test_<hex>` ao lado e o dropa
no fim.
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url

_SERVER = Path(__file__).resolve().parents[2]            # apps/server
ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL não definida")


def _sync(url: str) -> str:
    return url.replace("+asyncpg", "").replace("+psycopg2", "")


# --------------------------------------------------------------------------- #
# Banco descartável                                                            #
# --------------------------------------------------------------------------- #
def criar_banco() -> str:
    """Cria um banco vazio (com pgvector) e devolve a URL síncrona dele."""
    nome = f"aiw_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_sync(ADMIN_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{nome}"'))
    admin.dispose()
    return make_url(_sync(ADMIN_URL)).set(database=nome).render_as_string(hide_password=False)


def dropar_banco(url: str) -> None:
    nome = make_url(url).database
    admin = create_engine(_sync(ADMIN_URL), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def banco() -> Iterator[str]:
    url = criar_banco()
    try:
        yield url
    finally:
        dropar_banco(url)


@pytest.fixture
def engine(banco: str) -> Iterator[Engine]:
    eng = create_engine(banco)
    try:
        yield eng
    finally:
        eng.dispose()


# --------------------------------------------------------------------------- #
# Alembic                                                                      #
# --------------------------------------------------------------------------- #
def alembic_config():
    """Config SEM arquivo .ini: o env.py não chama fileConfig (que reconfiguraria o
    logging do processo de testes inteiro)."""
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_SERVER / "alembic"))
    return cfg


def revisoes_em_ordem() -> list[str]:
    """base → head."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    return [s.revision for s in reversed(list(script.walk_revisions("base", "heads")))]


def migrar(engine: Engine, destino: str = "head", *, descer: bool = False) -> None:
    """upgrade/downgrade numa transação — igual ao boot (env.py: tudo ou nada)."""
    from alembic import command

    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        (command.downgrade if descer else command.upgrade)(cfg, destino)


def revisao_atual(engine: Engine) -> str | None:
    with engine.connect() as c:
        existe = c.execute(text("SELECT to_regclass('public.alembic_version')")).scalar()
        if not existe:
            return None
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar()


# --------------------------------------------------------------------------- #
# Leitura do esquema                                                           #
# --------------------------------------------------------------------------- #
def tabelas(conn: Connection) -> list[str]:
    return list(conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename <> 'alembic_version' ORDER BY tablename"
    )).scalars())


def contagens(conn: Connection) -> dict[str, int]:
    return {
        t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()
        for t in tabelas(conn)
    }


def _colunas(conn: Connection, tabela: str) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text("""
        SELECT a.attname AS nome,
               NOT a.attnotnull AS nula,
               format_type(a.atttypid, a.atttypmod) AS tipo,
               t.typname AS udt,
               t.typtype AS typtype,
               pg_get_expr(d.adbin, d.adrelid) AS padrao,
               a.attidentity <> '' OR a.attgenerated <> '' AS gerada
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_type t ON t.oid = a.atttypid
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE c.relname = :t AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """), {"t": tabela})]


def _fks(conn: Connection, tabela: str) -> dict[str, tuple[str, str]]:
    """{coluna: (tabela_alvo, coluna_alvo)} — só FKs de uma coluna."""
    out: dict[str, tuple[str, str]] = {}
    for r in conn.execute(text("""
        SELECT a.attname AS col, cf.relname AS alvo, af.attname AS col_alvo
        FROM pg_constraint k
        JOIN pg_class c  ON c.oid = k.conrelid
        JOIN pg_class cf ON cf.oid = k.confrelid
        JOIN pg_attribute a  ON a.attrelid = k.conrelid  AND a.attnum = k.conkey[1]
        JOIN pg_attribute af ON af.attrelid = k.confrelid AND af.attnum = k.confkey[1]
        WHERE k.contype = 'f' AND c.relname = :t AND array_length(k.conkey, 1) = 1
    """), {"t": tabela}):
        out[r.col] = (r.alvo, r.col_alvo)
    return out


def _checks(conn: Connection, tabela: str) -> list[str]:
    return list(conn.execute(text("""
        SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        WHERE k.contype = 'c' AND c.relname = :t
    """), {"t": tabela}).scalars())


def _valores_permitidos(checks: list[str], coluna: str) -> list[str]:
    """Valores literais que um CHECK aceita para a coluna (`col IN (...)` / `= ANY`)."""
    for chk in checks:
        if not re.search(rf"\b{re.escape(coluna)}\b", chk):
            continue
        valores = re.findall(r"'((?:[^']|'')*)'::", chk)
        if valores:
            return [v.replace("''", "'") for v in valores]
    return []


# --------------------------------------------------------------------------- #
# Semeador genérico                                                            #
# --------------------------------------------------------------------------- #
LINHAS_POR_TABELA = 2


def _valor(conn: Connection, col: dict, i: int, tabela: str, checks: list[str]):
    permitidos = _valores_permitidos(checks, col["nome"])
    if permitidos:
        return permitidos[i % len(permitidos)]
    udt, tipo = col["udt"], col["tipo"]
    if col["typtype"] == "e":   # ENUM do Postgres
        rotulos = list(conn.execute(text(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :u ORDER BY enumsortorder"), {"u": udt}).scalars())
        return rotulos[i % len(rotulos)]
    if udt == "uuid":
        return str(uuid.uuid4())
    if udt in ("varchar", "bpchar", "text", "citext", "name"):
        base = f"{tabela[:6]}-{col['nome'][:6]}-{i}-{uuid.uuid4().hex[:8]}"
        m = re.search(r"\((\d+)\)", tipo)
        return base[-int(m.group(1)):] if m else base
    if udt in ("int2", "int4", "int8"):
        return i + 1
    if udt in ("float4", "float8", "numeric"):
        return 1
    if udt == "bool":
        return i % 2 == 1
    if udt in ("timestamptz", "timestamp"):
        return datetime.now(UTC)
    if udt == "date":
        return datetime.now(UTC).date()
    if udt == "time":
        return "12:00"
    if udt == "interval":
        return "1 hour"
    if udt in ("json", "jsonb"):
        return "{}"
    if udt == "bytea":
        return b"\x00seed"
    if udt == "inet":
        return "127.0.0.1"
    if udt == "tsvector":
        return ""
    if udt == "vector":
        dim = int(re.search(r"\((\d+)\)", tipo).group(1))
        return "[" + ",".join(["0.1"] * dim) + "]"
    if udt.startswith("_"):   # arrays
        return "{}"
    raise NotImplementedError(f"{tabela}.{col['nome']}: tipo {tipo!r} sem valor de semente")


def _ordem_topologica(conn: Connection, nomes: list[str]) -> list[str]:
    deps = {t: {alvo for alvo, _ in _fks(conn, t).values() if alvo != t} for t in nomes}
    ordem, feitos = [], set()
    while len(ordem) < len(nomes):
        prontos = [t for t in nomes if t not in feitos and deps[t] <= feitos | {t}]
        if not prontos:   # ciclo: quebra pelo primeiro que faltar
            prontos = [next(t for t in nomes if t not in feitos)]
        for t in prontos:
            ordem.append(t)
            feitos.add(t)
    return ordem


def semear(conn: Connection) -> dict[str, str]:
    """Garante LINHAS_POR_TABELA linhas em TODA tabela, preenchendo inclusive as colunas
    opcionais (quanto mais coluna com dado, mais uma migração tem como tropeçar).

    A linha i de cada tabela aponta, nas FKs, para a linha i do alvo — então a linha 0
    de tudo pertence ao "usuário 0" e a linha 1 ao "usuário 1" (cadeias separadas, o
    que o teste de exclusão de conta usa).

    Devolve {tabela: erro} das tabelas que não deu para semear (o chamador decide)."""
    erros: dict[str, str] = {}
    for tabela in _ordem_topologica(conn, tabelas(conn)):
        existentes = conn.execute(text(f'SELECT count(*) FROM "{tabela}"')).scalar_one()
        if existentes >= LINHAS_POR_TABELA:
            continue
        cols = _colunas(conn, tabela)
        fks = _fks(conn, tabela)
        checks = _checks(conn, tabela)
        for i in range(existentes, LINHAS_POR_TABELA):
            linha = {}
            for col in cols:
                if col["gerada"] or (col["padrao"] or "").startswith("nextval("):
                    continue
                if col["nome"] in fks:
                    alvo, col_alvo = fks[col["nome"]]
                    if alvo == tabela:
                        continue   # auto-referência: fica NULL (ou default)
                    refs = list(conn.execute(text(
                        f'SELECT "{col_alvo}" FROM "{alvo}" ORDER BY ctid')).scalars())
                    if not refs:
                        if col["nula"]:
                            continue
                        erros[tabela] = f"FK {col['nome']} → {alvo} sem linhas"
                        break
                    linha[col["nome"]] = refs[i % len(refs)]
                    continue
                if col["padrao"] is not None and col["nome"] in ("created_at", "updated_at"):
                    continue
                linha[col["nome"]] = _valor(conn, col, i, tabela, checks)
            else:
                nomes = ", ".join(f'"{c}"' for c in linha)
                params = ", ".join(f":p{n}" for n in range(len(linha)))
                sp = conn.begin_nested()
                try:
                    conn.execute(
                        text(f'INSERT INTO "{tabela}" ({nomes}) VALUES ({params})'),
                        {f"p{n}": v for n, v in enumerate(linha.values())},
                    )
                    sp.commit()
                except Exception as exc:  # noqa: BLE001 - reportado ao chamador
                    sp.rollback()
                    erros[tabela] = str(exc).splitlines()[0][:300]
                    break
                continue
            break
    return erros


def arquivo_da_revisao(rev: str) -> str:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    return Path(script.get_revision(rev).path).read_text(encoding="utf-8")
