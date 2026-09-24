"""Teto do corpo das requisições: vale para JSON, mas não para as rotas que gravam o
arquivo no disco aos pedaços (anexos e restauração de backup, que passa de gigabytes)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aiworkspace import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.get_settings(), "max_json_body_bytes", 64, raising=False)
    return TestClient(main.create_app())  # sem lifespan: só o middleware interessa


def test_corpo_grande_em_rota_json_e_recusado(client):
    r = client.post("/chats", content=b"x" * 500, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_restauracao_de_backup_nao_tem_teto(client):
    r = client.post("/admin/restore", files={"file": ("b.backup", b"x" * 500)})
    assert r.status_code != 413  # passa do middleware (aqui cai no login: 401)
