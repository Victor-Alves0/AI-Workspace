"""Refinamento L1 do grafo de código é opcional: o jedi (extra `graph-refine`) não vai
no app desktop — sem ele a rota diz que está indisponível, em vez de falhar em
background sem ninguém ver."""
from __future__ import annotations

import asyncio
import types
import uuid

import pytest
from fastapi import HTTPException

from aiworkspace import codespace_routes
from aiworkspace.codespace import graph_service


def _chamar(monkeypatch, disponivel: bool) -> list:
    iniciados: list = []
    projeto = types.SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), index_status="ready")

    async def dono(db, user, project_id):
        return projeto

    monkeypatch.setattr(codespace_routes, "_owned_project", dono)
    monkeypatch.setattr(graph_service, "refine_available", lambda: disponivel)
    monkeypatch.setattr(codespace_routes, "_spawn_refine", lambda pid, uid: iniciados.append(pid))
    asyncio.run(codespace_routes.refine_project(projeto.id, user=object(), db=None))
    return iniciados


def test_sem_jedi_a_rota_responde_indisponivel(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _chamar(monkeypatch, disponivel=False)
    assert exc.value.status_code == 501


def test_com_jedi_o_refinamento_comeca(monkeypatch):
    assert len(_chamar(monkeypatch, disponivel=True)) == 1


def test_disponibilidade_segue_o_jedi_instalado():
    import importlib.util

    assert graph_service.refine_available() == (importlib.util.find_spec("jedi") is not None)
