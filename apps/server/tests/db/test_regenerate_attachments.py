"""'Tentar novamente' com anexo: a mensagem gravada guarda o anexo por REFERÊNCIA
(upload_id); o regenerar precisa resolvê-lo como o envio faz. Antes chegava ao modelo
só {type, name, upload_id} — sem o texto —, como se o arquivo nunca tivesse sido enviado.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from .conftest import migrar, pytestmark  # noqa: F401


def test_anexo_gravado_chega_ao_turno_com_o_texto(banco, engine, monkeypatch):
    from aiworkspace import db as app_db
    from aiworkspace import uploads_service
    from aiworkspace.chat.turn_setup import _prepare_attachments
    from aiworkspace.models import Upload, User

    migrar(engine, "head")
    with Session(engine) as s:
        u = User(email=f"{uuid.uuid4().hex[:8]}@t.local", hashed_password="x")
        s.add(u)
        s.flush()
        up = Upload(user_id=u.id, filename="Texto colado.txt", mime="text/plain", size=30,
                    kind="file", path="", text="letra da música que colei aqui")
        s.add(up)
        s.commit()
        upload_id = str(up.id)

    eng = create_async_engine(banco.replace("postgresql://", "postgresql+asyncpg://", 1))
    monkeypatch.setattr(app_db, "SessionLocal", async_sessionmaker(eng, expire_on_commit=False))
    # exatamente o que fica gravado na mensagem (uploads_service.persistable)
    gravado = uploads_service.persistable(
        [{"type": "file", "name": "Texto colado.txt", "upload_id": upload_id}])

    async def rodar():
        try:
            return await _prepare_attachments(gravado, None)
        finally:
            await eng.dispose()

    [anexo] = asyncio.run(rodar())
    assert anexo["type"] == "file"
    assert anexo["text"] == "letra da música que colei aqui"


def test_regenerar_resolve_os_anexos_e_aceita_mensagem_so_com_anexo():
    from aiworkspace.chat import messages_routes as mr

    src = inspect.getsource(mr.regenerate_message)
    assert src.count("await _prepare_attachments(") == 2       # os dois ramos
    assert "if not user_text:" not in src                        # só anexo também refaz
