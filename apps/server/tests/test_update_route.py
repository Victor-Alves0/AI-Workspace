"""POST /admin/update: o servidor NÃO toca no Docker — só pede ao container updater."""
from __future__ import annotations

import asyncio
import types

import pytest
from fastapi import HTTPException

from aiworkspace import admin_routes

_ADMIN = types.SimpleNamespace(id="00000000-0000-0000-0000-000000000001", email="a@t.local")


@pytest.fixture
def respostas(monkeypatch):
    chamadas: list = []

    def usar(code, body=None):
        async def falso(method, path):
            chamadas.append((method, path))
            return code, body or {}
        monkeypatch.setattr(admin_routes, "_updater", falso)

    async def nada(*a, **k):
        return None

    monkeypatch.setattr(admin_routes.audit_service, "record", nada)
    return usar, chamadas


@pytest.mark.parametrize("code,esperado", [(0, 409), (409, 409), (500, 502)])
def test_updater_fora_do_ar_ou_ocupado(respostas, code, esperado):
    usar, _ = respostas
    usar(code)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_routes.request_update(admin=_ADMIN))
    assert exc.value.status_code == esperado


def test_pedido_aceito(respostas):
    usar, chamadas = respostas
    usar(202, {"ok": True})
    assert asyncio.run(admin_routes.request_update(admin=_ADMIN)) == {"ok": True}
    assert chamadas == [("POST", "/update")]


def test_sem_token_nao_ha_updater(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_routes.get_settings(), "updater_token_file", str(tmp_path / "nao-existe"),
                        raising=False)
    assert admin_routes._updater_token() is None
    assert asyncio.run(admin_routes._updater("GET", "/status")) == (0, {})
