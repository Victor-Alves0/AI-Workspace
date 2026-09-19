"""Exclusão de conta sobre o esquema real, com TODAS as tabelas populadas.

Duas promessas: (1) excluir funciona — nenhuma FK/relationship trava o DELETE, o que
deixaria o pedido de exclusão sem atendimento; (2) excluir UM usuário não leva dado
de OUTRO junto (cascade mal apontado apaga a conta errada).
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from .conftest import migrar, pytestmark, semear  # noqa: F401


def _colunas_de_dono(conn) -> list[tuple[str, str]]:
    """(tabela, coluna) de toda FK que aponta para users.id."""
    return [tuple(r) for r in conn.execute(text("""
        SELECT cl.relname, a.attname
        FROM pg_constraint k
        JOIN pg_class cl ON cl.oid = k.conrelid
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
        WHERE k.contype = 'f' AND k.confrelid = 'users'::regclass
        ORDER BY 1, 2
    """))]


def _linhas_do_usuario(conn, uid: str) -> dict[str, int]:
    return {
        f"{t}.{c}": conn.execute(
            text(f'SELECT count(*) FROM "{t}" WHERE "{c}" = :u'), {"u": uid}
        ).scalar_one()
        for t, c in _colunas_de_dono(conn)
    }


async def _excluir_como_o_endpoint(url: str, user_id: uuid.UUID) -> None:
    """Mesmo caminho do DELETE /admin/users/{id}: `db.delete(u)` + commit, no ORM
    async — relationships com lazy-load estouram aqui (MissingGreenlet), não no SQL."""
    from aiworkspace.models import User

    eng = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            u = await db.get(User, user_id)
            assert u is not None
            await db.delete(u)
            await db.commit()
    finally:
        await eng.dispose()


def test_excluir_um_usuario_apaga_so_o_que_e_dele(banco, engine):
    migrar(engine, "head")
    with engine.begin() as c:
        assert not semear(c)
        a, b = [str(x) for x in c.execute(text("SELECT id FROM users ORDER BY ctid")).scalars()]
        dono_a = _linhas_do_usuario(c, a)
        dono_b = _linhas_do_usuario(c, b)
    assert any(dono_a.values()), "semente não deu linhas ao usuário A (teste não provaria nada)"

    asyncio.run(_excluir_como_o_endpoint(banco, uuid.UUID(a)))

    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": a}).scalar() == 0
        sobras_a = {k: n for k, n in _linhas_do_usuario(c, a).items() if n}
        depois_b = _linhas_do_usuario(c, b)

    assert not sobras_a, f"linhas do usuário excluído ficaram para trás: {sobras_a}"
    perdidas_b = {k: (dono_b[k], depois_b[k]) for k in dono_b if depois_b[k] < dono_b[k]}
    assert not perdidas_b, f"excluir A apagou dados de B: {perdidas_b}"
