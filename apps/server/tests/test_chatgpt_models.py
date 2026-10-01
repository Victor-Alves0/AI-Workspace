"""Assinatura ChatGPT: os seletores mostram TODOS os modelos que a conta libera,
lidos do /models do backend do Codex (o mesmo que o Codex CLI consulta)."""
from __future__ import annotations

import asyncio

from aiworkspace.integrations import chatgpt_service as cs


def test_le_slugs_e_ignora_ocultos_e_fora_da_api():
    data = {"models": [
        {"slug": "gpt-5.5", "visibility": "list"},
        {"slug": "gpt-5.4-mini"},
        {"slug": "interno-x", "visibility": "hide"},
        {"slug": "so-no-app", "supported_in_api": False},
        {"slug": "gpt-5.5"},  # duplicado
    ]}
    assert cs._parse_models(data) == ["gpt-5.5", "gpt-5.4-mini"]


def test_formato_alternativo_data_id():
    assert cs._parse_models({"data": [{"id": "gpt-5.4"}, {"id": ""}]}) == ["gpt-5.4"]


def test_resposta_inesperada_nao_quebra():
    assert cs._parse_models({"erro": 1}) == [] and cs._parse_models(None) == []


def test_sem_resposta_do_chatgpt_cai_na_lista_salva(monkeypatch):
    cs._models_cache.clear()

    async def falha(uid):
        raise RuntimeError("ChatGPT não conectado")

    async def linha(uid):
        return {"models": ["gpt-5", "gpt-5-codex-fast"]}

    monkeypatch.setattr(cs, "get_access", falha)
    monkeypatch.setattr(cs, "_load_row", linha)

    # o "gpt-5" aposentado sai da reserva (a conta recusa com HTTP 400)
    assert asyncio.run(cs.available_models("u1")) == ["gpt-5-codex-fast"]


def test_reserva_prefere_a_ultima_lista_boa_do_chatgpt(monkeypatch):
    cs._models_cache.clear()

    async def falha(uid):
        raise RuntimeError("fora do ar")

    async def linha(uid):
        return {"models": ["gpt-5"], "models_live": ["gpt-6-sol", "gpt-5.5"]}

    monkeypatch.setattr(cs, "get_access", falha)
    monkeypatch.setattr(cs, "_load_row", linha)
    assert asyncio.run(cs.available_models("u1")) == ["gpt-6-sol", "gpt-5.5"]


def test_lista_ao_vivo_usa_a_versao_atual_do_codex_e_guarda(monkeypatch):
    """O /models filtra por versão do cliente: com a velha "0.99.0" fixa a lista vinha
    vazia e o app caía em modelos aposentados."""
    cs._models_cache.clear()
    pedidos, guardados = [], []

    async def acesso(uid):
        return "tok", "acc"

    async def versao():
        return "0.159.3"

    async def guarda(uid, models):
        guardados.append(models)

    class Resp:
        status_code = 200

        def json(self):
            return {"models": [{"slug": "gpt-6-sol", "visibility": "list"},
                               {"slug": "gpt-reserve", "visibility": "hide"},
                               {"slug": "gpt-5.5", "visibility": "list"}]}

    class Cliente:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            pedidos.append(params)
            return Resp()

    monkeypatch.setattr(cs, "get_access", acesso)
    monkeypatch.setattr(cs, "client_version", versao)
    monkeypatch.setattr(cs, "_save_live_models", guarda)
    monkeypatch.setattr(cs.httpx, "AsyncClient", Cliente)
    assert asyncio.run(cs.available_models("u1")) == ["gpt-6-sol", "gpt-5.5"]
    assert pedidos == [{"client_version": "0.159.3"}] and guardados == [["gpt-6-sol", "gpt-5.5"]]


def test_versao_do_codex_vem_do_npm_e_tem_reserva(monkeypatch):
    cs._version_cache.clear()

    class Resp:
        def __init__(self, status, data):
            self.status_code, self._d = status, data

        def json(self):
            return self._d

    respostas = [Resp(200, {"version": "0.200.1"})]

    class Cliente:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return respostas.pop(0)

    monkeypatch.setattr(cs.httpx, "AsyncClient", Cliente)
    assert asyncio.run(cs.client_version()) == "0.200.1"
    assert asyncio.run(cs.client_version()) == "0.200.1"  # cache: não pergunta de novo
    cs._version_cache.clear()
    respostas.append(Resp(200, {"version": "lixo"}))
    assert asyncio.run(cs.client_version()) == cs._CODEX_VERSION_FALLBACK
    cs._version_cache.clear()
