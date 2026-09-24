"""Voz embutida (Kokoro no próprio processo) — substituiu o container do Kokoro.

Roteamento: "builtin:…" (ou provedor "builtin") vai para ela; no automático ela só
entra quando o usuário não tem servidor local nem chave de API de voz."""
from __future__ import annotations

import asyncio
import io
import types
import wave

import pytest

from aiworkspace import voice_routes
from aiworkspace.integrations import voice_builtin as vb

_USER = types.SimpleNamespace(id="00000000-0000-0000-0000-000000000001")


def _cenario(monkeypatch, *, local=None, chave=None, instalado=True):
    async def provedor(db, uid):
        return local

    async def segredo(db, uid, nome):
        return chave

    monkeypatch.setattr(voice_routes.voice_service, "get_provider", provedor)
    monkeypatch.setattr(voice_routes, "get_secret", segredo)
    monkeypatch.setattr(vb, "available", lambda: instalado)


def _usa(provider, voice):
    return asyncio.run(voice_routes._use_builtin(None, _USER, provider, voice))


def test_quem_nao_configurou_nada_ganha_a_voz_embutida(monkeypatch):
    _cenario(monkeypatch)
    assert _usa("auto", "alloy") is True


def test_servidor_local_ou_chave_de_api_continuam_valendo(monkeypatch):
    _cenario(monkeypatch, local={"base_url": "http://kokoro:8880/v1"})
    assert _usa("auto", "af_bella") is False
    _cenario(monkeypatch, chave="sk-voz")
    assert _usa("auto", "alloy") is False


def test_voz_embutida_escolhida_explicitamente(monkeypatch):
    _cenario(monkeypatch, chave="sk-voz")
    assert _usa("auto", "builtin:pm_alex") is True
    assert _usa("builtin", "qualquer") is True


def test_sem_o_pacote_escolha_explicita_explica(monkeypatch):
    from fastapi import HTTPException

    _cenario(monkeypatch, instalado=False)
    assert _usa("auto", "alloy") is False
    with pytest.raises(HTTPException):
        _usa("auto", "builtin:pf_dora")


def test_lingua_pelo_prefixo_e_vozes_sem_baixar_nada(monkeypatch):
    monkeypatch.setattr(vb, "available", lambda: True)
    assert vb.lang_for("pf_dora") == "pt-br" and vb.lang_for("af_bella") == "en-us"
    assert vb.voices()[:3] == ["pf_dora", "pm_alex", "pm_santa"]


def test_sintese_devolve_wav_e_troca_voz_desconhecida(monkeypatch):
    chamadas = []

    class _Eng:
        def create(self, text, voice, speed, lang):
            import numpy as np
            chamadas.append((voice, lang))
            return np.zeros(2400, dtype="float32"), 24000

    monkeypatch.setattr(vb, "_engine", lambda: _Eng())
    data, mime = vb.synthesize("oi", "builtin:alloy")

    assert mime == "audio/wav" and chamadas == [("pf_dora", "pt-br")]
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400
