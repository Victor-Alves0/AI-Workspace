"""Um processo servidor por banco — premissa VERIFICADA, não só documentada.

Várias peças guardam estado vivo na memória do processo: as gerações em andamento
(``chat/generation.py``: F5, parar, fila e steer acham a geração por ali), o scheduler
das automações, os pollers dos canais, os jobs de exec. Com dois processos no mesmo
banco (``uvicorn --workers 4``, duas réplicas), cada um teria o seu registro: o "parar"
não acharia a geração, o mesmo chat rodaria dois turnos, a mesma automação dispararia
duas vezes, a mesma mensagem de canal seria respondida em dobro.

No boot, o processo pega um advisory lock do Postgres numa conexão que fica aberta
enquanto ele vive. Um segundo processo espera um pouco (o anterior pode estar saindo
num restart) e, se o lock continuar ocupado, se recusa a subir com uma mensagem clara.
Se o processo morre, a conexão cai e o Postgres solta o lock sozinho.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# chave fixa do lock ("AIWS" + 1), dentro do int8 com sinal
LOCK_KEY = 0x41495753_00000001

_conn: Any = None


class AlreadyRunning(RuntimeError):
    pass


async def acquire(engine: Any, *, wait_seconds: float = 45.0, poll: float = 1.0) -> bool:
    """Pega o lock (True). Levanta AlreadyRunning se outro processo o segura além da
    espera. Banco indisponível/não-Postgres → False (o resto do boot lida com isso)."""
    global _conn
    if _conn is not None:
        return True
    if getattr(getattr(engine, "dialect", None), "name", "") != "postgresql":
        return False
    deadline = time.monotonic() + wait_seconds
    avisou = False
    while True:
        try:
            conn = await engine.connect()
        except Exception as exc:  # noqa: BLE001 - sem banco: o self-check alarma
            logger.warning("Lock de instância única indisponível (%s)", exc)
            return False
        try:
            got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})).scalar()
            # a conexão fica "idle in transaction" se o autobegin abrir uma; fecha a
            # transação e mantém a CONEXÃO (é ela que segura o lock de sessão)
            await conn.commit()
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            logger.warning("Lock de instância única falhou (%s)", exc)
            return False
        if got:
            _conn = conn
            return True
        await conn.close()
        if time.monotonic() >= deadline:
            raise AlreadyRunning(
                "Outro processo do AI Workspace já está rodando com este banco de dados. "
                "O servidor precisa rodar em UM processo (sem `--workers` > 1 nem réplicas): "
                "as gerações em andamento, o agendador e os canais vivem na memória dele."
            )
        if not avisou:
            logger.warning("Outro processo segura o lock de instância única; aguardando até %ss", int(wait_seconds))
            avisou = True
        await asyncio.sleep(poll)


async def release() -> None:
    global _conn
    conn, _conn = _conn, None
    if conn is None:
        return
    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        await conn.commit()
    except Exception:  # noqa: BLE001 - fechar a conexão solta o lock de qualquer jeito
        pass
    try:
        await conn.close()
    except Exception:  # noqa: BLE001
        pass
