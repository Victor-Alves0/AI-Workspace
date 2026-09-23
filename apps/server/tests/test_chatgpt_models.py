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

    assert asyncio.run(cs.available_models("u1")) == ["gpt-5", "gpt-5-codex-fast"]
