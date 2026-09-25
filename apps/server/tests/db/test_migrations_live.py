"""Migrações contra Postgres de verdade (ver conftest.py: pula sem TEST_DATABASE_URL).

O lint (tests/test_migration_lint.py) lê o código; aqui o SQL RODA — sobre banco
vazio, sobre banco com dados em cada degrau da corrente, e contra os modelos.
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from ..test_migration_lint import MARCADOR_DESTRUTIVO
from .conftest import (
    sync_url,
    arquivo_da_revisao,
    contagens,
    migrar,
    pytestmark,  # noqa: F401 - aplica o skip a este módulo
    revisao_atual,
    revisoes_em_ordem,
    semear,
)


def test_upgrade_do_zero_ate_a_cabeca(engine):
    """Instalação nova: a corrente inteira numa transação só, como no boot."""
    migrar(engine, "head")
    assert revisao_atual(engine) == revisoes_em_ordem()[-1]

    # boot seguinte (nada pendente) não pode falhar nem mexer em nada
    migrar(engine, "head")
    assert revisao_atual(engine) == revisoes_em_ordem()[-1]


def test_cada_migracao_roda_sobre_dados_e_nao_perde_linhas(engine):
    """O teste que teria pego o 0078: sobe UM degrau por vez, com TODAS as tabelas
    populadas (inclusive colunas opcionais) antes de cada passo. Cobre o que o banco
    vazio do desenvolvedor esconde — NOT NULL sem default, UNIQUE novo sobre dados
    repetidos, troca de tipo que não converte, backfill que quebra — e cobra que
    nenhuma migração some com linhas sem ter declarado isso."""
    perdas: list[str] = []
    for rev in revisoes_em_ordem():
        with engine.begin() as c:
            if revisao_atual(engine) is not None:
                erros = semear(c)
                assert not erros, f"semeador não conseguiu popular antes de {rev}: {erros}"
            antes = contagens(c)

        try:
            migrar(engine, rev)
        except Exception as exc:
            raise AssertionError(f"migração {rev} FALHOU sobre um banco com dados: {exc}") from exc

        with engine.connect() as c:
            depois = contagens(c)
        if MARCADOR_DESTRUTIVO in arquivo_da_revisao(rev):
            continue
        for tabela, n in antes.items():
            if tabela not in depois:
                perdas.append(f"{rev}: tabela {tabela} ({n} linhas) sumiu")
            elif depois[tabela] < n:
                perdas.append(f"{rev}: {tabela} {n} → {depois[tabela]} linhas")
    assert not perdas, f"migração apagou dados sem `{MARCADOR_DESTRUTIVO}`: {perdas}"


def test_downgrade_e_upgrade_de_volta_com_dados(engine):
    """Rollback de deploy: descer as migrações recentes (com dados) e subir de novo
    precisa funcionar — é a saída de emergência quando uma versão nova dá problema."""
    revs = revisoes_em_ordem()
    alvo = revs[-11]   # desce as 10 últimas
    migrar(engine, "head")
    with engine.begin() as c:
        assert not semear(c)
    migrar(engine, alvo, descer=True)
    assert revisao_atual(engine) == alvo
    migrar(engine, "head")
    assert revisao_atual(engine) == revs[-1]


def test_modelos_batem_com_o_banco_migrado(engine):
    """Modelo pedindo algo que a migração não criou = erro 500 em produção na primeira
    query ("column does not exist"), só depois do deploy. Compara o metadata dos
    modelos com o banco que as migrações realmente produzem.

    Diferenças "o banco tem a mais" (índice/constraint/tabela criados por SQL cru) são
    toleradas quando não podem quebrar o ORM; o resto falha."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import aiworkspace.models  # noqa: F401 - registra as tabelas
    from aiworkspace.db import Base

    migrar(engine, "head")
    with engine.connect() as c:
        diffs = compare_metadata(
            MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata
        )
        insp = inspect(c)
        unicos_no_banco = {
            (t, tuple(u["column_names"]))
            for t in insp.get_table_names()
            for u in insp.get_unique_constraints(t)
        } | {
            (t, tuple(i["column_names"]))
            for t in insp.get_table_names()
            for i in insp.get_indexes(t) if i.get("unique")
        }
        colunas = {
            (t, col["name"]): col
            for t in insp.get_table_names() for col in insp.get_columns(t)
        }

    problemas: list[str] = []
    for d in diffs:
        if isinstance(d, list):          # modify_* vem agrupado numa lista
            for m in d:
                kind, _, tabela, coluna = m[0], m[1], m[2], m[3]
                if kind == "modify_nullable":
                    no_banco_nulo, no_modelo_nulo = m[5], m[6]
                    if no_banco_nulo is False and no_modelo_nulo is True:
                        problemas.append(f"{tabela}.{coluna}: NOT NULL no banco, opcional no modelo")
                elif kind == "modify_type":
                    problemas.append(f"{tabela}.{coluna}: tipo {m[5]} no banco, {m[6]} no modelo")
                else:
                    problemas.append(f"{kind} {tabela}.{coluna}")
            continue

        kind = d[0]
        if kind in ("remove_index", "remove_constraint"):
            continue                     # objeto extra só no banco: não quebra o ORM
        if kind == "remove_table":
            continue                     # tabela acessada por SQL cru (ex.: disabled_memories)
        if kind == "remove_column":
            tabela, col = d[2], d[3]
            info = colunas.get((tabela, col.name), {})
            if not info.get("nullable", True) and info.get("default") is None:
                problemas.append(
                    f"{tabela}.{col.name}: NOT NULL sem default no banco e ausente do "
                    "modelo — todo INSERT do ORM falha"
                )
            continue
        if kind == "add_index" and d[1].unique:
            idx = d[1]
            chave = (idx.table.name, tuple(c.name for c in idx.columns))
            if chave in unicos_no_banco:
                continue                 # mesma garantia, declarada como constraint
        if kind == "add_table":
            problemas.append(f"tabela {d[1].name} existe no modelo e não no banco")
        elif kind == "add_column":
            problemas.append(f"coluna {d[2]}.{d[3].name} existe no modelo e não no banco")
        else:
            problemas.append(repr(d)[:200])
    assert not problemas, "modelo e migrações divergem:\n  " + "\n  ".join(problemas)


def test_toda_tabela_tem_chave_primaria(engine):
    """Sem PK, UPDATE/DELETE do ORM não sabe achar a linha, e o pg_restore de uma
    linha duplicada passa calado."""
    migrar(engine, "head")
    with engine.connect() as c:
        sem_pk = list(c.execute(text("""
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
            WHERE c.relkind = 'r' AND NOT EXISTS (
                SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'p')
        """)).scalars())
    assert not sem_pk, f"tabelas sem chave primária: {sem_pk}"


def test_toda_fk_para_usuario_define_o_que_acontece_na_exclusao(engine):
    """FK para `users` sem ON DELETE (= NO ACTION) trava a exclusão da conta assim que
    o usuário tiver uma linha naquela tabela — o admin clica em excluir e recebe 500,
    e o pedido de exclusão (LGPD) não é atendido."""
    migrar(engine, "head")
    with engine.connect() as c:
        travadas = list(c.execute(text("""
            SELECT cl.relname || '.' || a.attname
            FROM pg_constraint k
            JOIN pg_class cl ON cl.oid = k.conrelid
            JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
            WHERE k.contype = 'f' AND k.confrelid = 'users'::regclass
              AND k.confdeltype IN ('a', 'r')
        """)).scalars())
    assert not travadas, f"FK p/ users sem ON DELETE CASCADE/SET NULL: {travadas}"


def test_banco_semeado_sobrevive_ao_boot_duas_vezes(banco):
    """Reinício com tudo em dia (o caso de todo restart): nada roda, nada muda."""
    eng = create_engine(sync_url(banco))
    try:
        migrar(eng, "head")
        with eng.begin() as c:
            assert not semear(c)
            antes = contagens(c)
        migrar(eng, "head")
        with eng.connect() as c:
            assert contagens(c) == antes
    finally:
        eng.dispose()
