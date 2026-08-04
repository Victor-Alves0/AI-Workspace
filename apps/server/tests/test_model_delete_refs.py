"""Apagar um ModelConfig não pode deixar referência "custom:<id>" pendurada.

Bug real (print do usuário): o seletor exibia `custom:b15bfddd-57c0-4…` como se fosse o
nome de um modelo, marcado "Modelo padrão ✓". Causa: `users.default_model` guardava
"custom:<id>" do modelo APAGADO; o front não achava o ModelConfig e caía num fallback que
usava a string crua. Aqui garantimos a limpeza na origem (o DELETE do modelo).
"""
from __future__ import annotations

import uuid

from aiworkspace.models_routes import _clear_model_references


class _FakeUser:
    def __init__(self, default_model, profile):
        self.id = uuid.uuid4()
        self.default_model = default_model
        self.profile = profile


class _FakeProject:
    def __init__(self, default_model):
        self.default_model = default_model


class _FakeDB:
    """Só precisa responder ao `scalars(select(CodespaceProject)...)`."""
    def __init__(self, projects):
        self._projects = projects

    async def scalars(self, _stmt):
        return list(self._projects)


async def test_limpa_padrao_fixados_favoritos_e_projeto():
    mid = str(uuid.uuid4())
    ref = f"custom:{mid}"
    outro = f"custom:{uuid.uuid4()}"
    user = _FakeUser(ref, {
        "pinned_models": [ref, outro, "ext:gpt-4"],
        "favorite_models": [ref],
        "outra_chave": "preservar",
    })
    proj = _FakeProject(ref)
    await _clear_model_references(_FakeDB([proj]), user, mid)

    assert user.default_model is None                      # padrão do usuário limpo
    assert user.profile["pinned_models"] == [outro, "ext:gpt-4"]   # só o alvo sai
    assert user.profile["favorite_models"] == []
    assert user.profile["outra_chave"] == "preservar"      # não destrói o resto do perfil
    assert proj.default_model is None                      # padrão do projeto limpo


async def test_nao_mexe_no_que_nao_referencia_o_modelo():
    mid = str(uuid.uuid4())
    outro = f"custom:{uuid.uuid4()}"
    user = _FakeUser(outro, {"pinned_models": [outro], "favorite_models": []})
    proj = _FakeProject(outro)
    await _clear_model_references(_FakeDB([proj]), user, mid)

    assert user.default_model == outro
    assert user.profile["pinned_models"] == [outro]
    assert proj.default_model == outro


async def test_perfil_vazio_ou_estranho_nao_quebra():
    mid = str(uuid.uuid4())
    for profile in ({}, None, {"pinned_models": "nao-e-lista"}):
        user = _FakeUser(None, profile)
        await _clear_model_references(_FakeDB([]), user, mid)  # não pode levantar
        assert user.default_model is None
