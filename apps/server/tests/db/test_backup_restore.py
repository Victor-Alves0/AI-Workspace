"""Backup → restore com os MESMOS comandos do painel (aiworkspace/db_restore.py).

Restaurar é a operação mais perigosa do sistema: substitui o banco vivo inteiro.
Aqui ela roda de verdade — incluindo o caso em que FALHA no meio.
Precisa de pg_dump/pg_restore/psql no PATH (ou em PG_BIN); sem eles, pula.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from aiworkspace import db_restore

from .conftest import (
    contagens,
    criar_banco,
    dropar_banco,
    migrar,
    pytestmark,  # noqa: F401
    revisao_atual,
    revisoes_em_ordem,
    semear,
    tabelas,
)


def _pasta_dos_binarios() -> str | None:
    pasta = os.environ.get("PG_BIN", "").strip()
    if pasta:
        return pasta
    achado = shutil.which("pg_restore")
    return str(Path(achado).parent) if achado else None


BIN = _pasta_dos_binarios()
precisa_binarios = pytest.mark.skipif(
    not (BIN and all(shutil.which(b, path=BIN) for b in ("pg_dump", "pg_restore", "psql"))),
    reason="pg_dump/pg_restore/psql indisponíveis (PATH ou PG_BIN)",
)


def _impressao_digital(url: str) -> dict[str, str]:
    """{tabela: md5 de todas as linhas} — igualdade = mesmo conteúdo, linha a linha."""
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return {
                t: c.execute(text(
                    f'SELECT md5(coalesce(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) '
                    f'FROM "{t}" x'
                )).scalar_one()
                for t in tabelas(c)
            }
    finally:
        eng.dispose()


def _dump(url: str, destino: Path) -> None:
    subprocess.run(db_restore.dump_argv(url, str(destino), BIN), check=True, capture_output=True)


def _restore(url: str, arquivo: Path, sabotagem: str = "") -> subprocess.CompletedProcess:
    """Os dois passos do painel (import_backup), na mesma ordem. `sabotagem` é SQL
    anexado ao FIM do script — simula a falha no último comando (disco cheio,
    conexão caída), o pior momento: tudo já foi dropado e recriado."""
    pasta = arquivo.parent / f"work-{arquivo.stem}"
    pasta.mkdir(exist_ok=True)
    script = str(pasta / "restore.sql")
    r = subprocess.run(
        db_restore.script_argv(str(arquivo), script, BIN), capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        return r
    if sabotagem:
        with open(script, "a", encoding="utf-8") as f:
            f.write(f"\n{sabotagem}\n")
    return subprocess.run(
        db_restore.apply_argv(url, db_restore.write_prelude(pasta), script, BIN),
        capture_output=True, text=True, check=False,
    )


@pytest.fixture
def dois_bancos():
    origem, destino = criar_banco(), criar_banco()
    try:
        yield origem, destino
    finally:
        dropar_banco(origem)
        dropar_banco(destino)


def _preparar(url: str, rev: str = "head") -> None:
    eng = create_engine(url)
    try:
        migrar(eng, rev)
        with eng.begin() as c:
            assert not semear(c)
    finally:
        eng.dispose()


@precisa_binarios
def test_restore_substitui_o_banco_pelo_backup_linha_a_linha(dois_bancos, tmp_path):
    origem, destino = dois_bancos
    _preparar(origem)
    _preparar(destino)            # destino já em uso, com OUTROS dados
    assert _impressao_digital(origem) != _impressao_digital(destino)

    arquivo = tmp_path / "backup.dump"
    _dump(origem, arquivo)
    r = _restore(destino, arquivo)
    assert r.returncode == 0, r.stderr[-2000:]

    assert _impressao_digital(destino) == _impressao_digital(origem)


@precisa_binarios
def test_restore_que_falha_no_meio_nao_toca_no_banco(dois_bancos, tmp_path):
    """O cenário que apagava dados: o restore esvazia o banco, recria quase tudo e
    falha no fim. Antes (pg_restore --clean sem transação) o banco ficava metade velho,
    metade apagado. Agora é uma transação só: o erro no ÚLTIMO comando desfaz até o
    DROP SCHEMA do primeiro."""
    origem, destino = dois_bancos
    _preparar(origem)
    _preparar(destino)
    antes = _impressao_digital(destino)

    arquivo = tmp_path / "backup.dump"
    _dump(origem, arquivo)
    r = _restore(destino, arquivo, sabotagem="SELECT 1/0;")

    assert r.returncode != 0, "o restore sabotado deveria ter falhado"
    assert _impressao_digital(destino) == antes, "restore que falhou alterou o banco"
    eng = create_engine(destino)
    try:
        assert revisao_atual(eng) == revisoes_em_ordem()[-1]
    finally:
        eng.dispose()


@precisa_binarios
def test_backup_de_versao_antiga_restaura_e_migra_ate_a_cabeca(dois_bancos, tmp_path):
    """Migração de máquina com backup antigo: restore + `alembic upgrade head` (o que o
    painel faz em seguida) sem perder linha."""
    origem, destino = dois_bancos
    antiga = revisoes_em_ordem()[-11]
    _preparar(origem, antiga)
    _preparar(destino)

    arquivo = tmp_path / "antigo.dump"
    _dump(origem, arquivo)
    r = _restore(destino, arquivo)
    assert r.returncode == 0, r.stderr[-2000:]

    eng = create_engine(destino)
    try:
        assert revisao_atual(eng) == antiga
        with eng.connect() as c:
            antes = contagens(c)
        migrar(eng, "head")
        assert revisao_atual(eng) == revisoes_em_ordem()[-1]
        with eng.connect() as c:
            depois = contagens(c)
    finally:
        eng.dispose()
    perdidas = {t: (n, depois.get(t)) for t, n in antes.items() if depois.get(t, 0) < n}
    assert not perdidas, f"linhas perdidas ao migrar o backup antigo: {perdidas}"


@precisa_binarios
def test_backup_corrompido_e_recusado_sem_tocar_no_banco(dois_bancos, tmp_path):
    """Upload truncado (conexão caiu no meio): o passo 1 recusa antes de encostar no banco."""
    origem, destino = dois_bancos
    _preparar(origem)
    _preparar(destino)
    antes = _impressao_digital(destino)

    inteiro = tmp_path / "inteiro.dump"
    _dump(origem, inteiro)
    truncado = tmp_path / "truncado.dump"
    dados = inteiro.read_bytes()
    truncado.write_bytes(dados[: len(dados) // 2])

    r = _restore(destino, truncado)
    assert r.returncode != 0
    assert _impressao_digital(destino) == antes
