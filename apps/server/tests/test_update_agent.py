"""Atualização pelo painel (servidor Docker): o container só grava um PEDIDO na pasta
montada do host; quem roda o update.sh é o agente do host (scripts/update-agent.sh)."""
from __future__ import annotations

import asyncio
import json
import types

import pytest
from fastapi import HTTPException

from aiworkspace import admin_routes
from aiworkspace.config import get_settings

_ADMIN = types.SimpleNamespace(id="00000000-0000-0000-0000-000000000001", email="a@t.local")


@pytest.fixture
def controle(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "update_control_dir", str(tmp_path), raising=False)

    async def nada(*a, **k):
        return None

    monkeypatch.setattr(admin_routes.audit_service, "record", nada)
    return tmp_path


def test_sem_agente_no_host_recusa(controle):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_routes.request_update(admin=_ADMIN))
    assert exc.value.status_code == 409
    assert not (controle / "update-request").exists()


def test_com_agente_grava_o_pedido(controle):
    (controle / "agent").write_text("x")

    assert asyncio.run(admin_routes.request_update(admin=_ADMIN)) == {"ok": True}
    pedido = json.loads((controle / "update-request").read_text())
    assert pedido["by"] == "a@t.local"


def test_nao_empilha_pedido_com_atualizacao_rodando(controle):
    (controle / "agent").write_text("x")
    (controle / "update-status.json").write_text(json.dumps({"state": "running"}))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin_routes.request_update(admin=_ADMIN))
    assert exc.value.status_code == 409


def test_estado_do_agente_e_do_ultimo_pedido(controle):
    assert admin_routes._update_agent_installed() is False and admin_routes._update_status() is None
    (controle / "agent").write_text("x")
    (controle / "update-status.json").write_text(json.dumps({"state": "done", "log": "ok"}))

    assert admin_routes._update_agent_installed() is True
    assert admin_routes._update_status() == {"state": "done", "log": "ok"}
