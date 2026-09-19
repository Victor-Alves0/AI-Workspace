"""Backup e restore do banco — os comandos exatos, num lugar só.

Usado pelo painel (admin_routes) e pela bateria de banco (tests/db/), que restaura
com EXATAMENTE estes argumentos.

Restore em dois tempos, nunca sobre o banco vivo às cegas:
  1. `pg_restore -f` converte o dump em script SQL (arquivo). Se o dump estiver
     corrompido, falha AQUI — antes de tocar no banco.
  2. `psql --single-transaction` aplica, numa transação só: esvazia o esquema
     `public` e roda o script. Qualquer erro → ROLLBACK, e o banco fica exatamente
     como estava.

Por que não `pg_restore --clean` direto no banco: o `--clean` só dropa o que EXISTE
NO DUMP. Um backup de versão anterior não conhece as tabelas criadas depois; elas
seguram FKs para `users` e o DROP do `users_pkey` é recusado. Sem transação única o
pg_restore seguia "com erros" e deixava o banco metade velho, metade apagado.
Esvaziando o `public` antes, o banco vira exatamente o backup — e o
`alembic upgrade head` seguinte recria o que for de versões posteriores.

O script intermediário vai para arquivo (e não por pipe) de propósito: com pipe, um
pg_restore que morresse no meio entregaria ao psql um script TRUNCADO, e o psql
comitaria a metade que recebeu.
"""
from __future__ import annotations

from pathlib import Path

PG_DUMP_FLAGS = ("--format=custom", "--no-owner", "--no-privileges")

# o esquema some inteiro (inclusive a extensão vector, que o script recria com
# CREATE EXTENSION IF NOT EXISTS); outros esquemas do banco não são tocados
PRELUDE_SQL = "DROP SCHEMA IF EXISTS public CASCADE;\nCREATE SCHEMA public;\n"


def _exe(nome: str, bin_dir: str | None) -> str:
    return str(Path(bin_dir) / nome) if bin_dir else nome


def dump_argv(url: str, destino: str, bin_dir: str | None = None) -> list[str]:
    return [_exe("pg_dump", bin_dir), *PG_DUMP_FLAGS, f"--file={destino}", f"--dbname={url}"]


def script_argv(dump: str, script: str, bin_dir: str | None = None) -> list[str]:
    """Passo 1: dump → script SQL em arquivo (não toca em banco nenhum)."""
    return [_exe("pg_restore", bin_dir), "--no-owner", "--no-privileges", "-f", script, dump]


def apply_argv(url: str, prelude: str, script: str, bin_dir: str | None = None) -> list[str]:
    """Passo 2: prelúdio + script numa transação só; o 1º erro aborta tudo."""
    return [
        _exe("psql", bin_dir), "--no-psqlrc", "--quiet", "--single-transaction",
        "--set=ON_ERROR_STOP=1", f"--dbname={url}", "-f", prelude, "-f", script,
    ]


def write_prelude(pasta: str | Path) -> str:
    caminho = Path(pasta) / "prelude.sql"
    caminho.write_text(PRELUDE_SQL, encoding="utf-8")
    return str(caminho)
