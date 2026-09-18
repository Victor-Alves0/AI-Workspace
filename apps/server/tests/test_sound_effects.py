"""Efeitos sonoros na narração: cache, trava e quando pedir o marcador ao modelo.

Cada som gerado custa créditos da ElevenLabs. O que estes testes protegem é o que
torna o recurso barato: a mesma descrição é gerada UMA vez (e reaproveitada), dois
pedidos simultâneos do mesmo som não geram duas vezes, e o modelo só recebe a
instrução de usar sons quando existe alguém para tocá-los.

Herméticos: banco e ElevenLabs são fakes.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from aiworkspace import sound_effects
from aiworkspace.integrations import elevenlabs_service
from aiworkspace.models import GeneratedImage, SoundEffect


def test_descricoes_equivalentes_sao_o_mesmo_som():
    """Maiúsculas, espaços e pontuação final não podem virar uma segunda geração paga."""
    a = sound_effects.cache_key("Heavy wooden door  slamming.")
    b = sound_effects.cache_key("heavy wooden door slamming")
    assert a == b
    assert a != sound_effects.cache_key("heavy wooden door creaking")


def test_descricao_gigante_e_cortada():
    assert len(sound_effects.normalize("x" * 5000)) == sound_effects.MAX_PROMPT_CHARS


def test_instrucao_ensina_o_marcador_com_rotulo():
    texto = sound_effects.INSTRUCTION
    assert "[[som:" in texto and "|" in texto
    assert "0–3" in texto            # uso comedido: som em toda frase vira ruído


@pytest.mark.asyncio
async def test_sem_elevenlabs_o_modelo_nem_recebe_a_instrucao(monkeypatch):
    """Pedir marcadores que ninguém consegue tocar geraria botões quebrados."""
    async def sem(_db, _uid):
        return None

    monkeypatch.setattr(elevenlabs_service, "get_provider", sem)
    assert await sound_effects.instruction_if_available(None, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_com_elevenlabs_a_instrucao_entra(monkeypatch):
    async def com(_db, _uid):
        return {"api_key": "k", "enabled": True}

    monkeypatch.setattr(elevenlabs_service, "get_provider", com)
    assert await sound_effects.instruction_if_available(None, uuid.uuid4()) == sound_effects.INSTRUCTION


class _FakeDb:
    """Sessão mínima: o cache "existe" depois que um SoundEffect é gravado."""

    def __init__(self):
        self.efeitos: list[SoundEffect] = []
        self.midias: list[GeneratedImage] = []

    async def scalar(self, _stmt):
        return self.efeitos[0] if self.efeitos else None

    def add(self, row):
        if isinstance(row, GeneratedImage):
            self.midias.append(row)
        elif isinstance(row, SoundEffect):
            self.efeitos.append(row)

    async def flush(self):
        for row in self.midias:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    async def commit(self):
        return None


def _conectar(monkeypatch, gerados: list):
    async def provedor(_db, _uid):
        return {"api_key": "k", "enabled": True}

    def gerar(_key, texto, duration=None):
        time.sleep(0.05)                 # simula a latência real da geração
        gerados.append(texto)
        return b"ID3-audio", "audio/mpeg"

    monkeypatch.setattr(elevenlabs_service, "get_provider", provedor)
    monkeypatch.setattr(elevenlabs_service, "sound_effect", gerar)


@pytest.mark.asyncio
async def test_primeiro_toque_gera_e_os_seguintes_vem_do_cache(monkeypatch):
    gerados: list = []
    _conectar(monkeypatch, gerados)
    db, uid = _FakeDb(), uuid.uuid4()

    primeiro = await sound_effects.get_or_generate(db, uid, "Goblin snarling")
    segundo = await sound_effects.get_or_generate(db, uid, "goblin snarling.")

    assert gerados == ["goblin snarling"]          # gerou UMA vez
    assert primeiro["cached"] is False and segundo["cached"] is True
    assert primeiro["url"].startswith("/images/")
    assert db.midias[0].mime == "audio/mpeg"


@pytest.mark.asyncio
async def test_autoplay_e_clique_simultaneos_nao_pagam_duas_vezes(monkeypatch):
    """O som toca sozinho e o usuário clica no mesmo botão antes de ele chegar: sem a
    trava, seriam duas gerações cobradas para o mesmo som."""
    gerados: list = []
    _conectar(monkeypatch, gerados)
    db, uid = _FakeDb(), uuid.uuid4()

    resultados = await asyncio.gather(
        sound_effects.get_or_generate(db, uid, "sword clash"),
        sound_effects.get_or_generate(db, uid, "sword clash"),
    )

    assert gerados == ["sword clash"]
    assert sorted(r["cached"] for r in resultados) == [False, True]


@pytest.mark.asyncio
async def test_sem_conexao_o_erro_diz_o_que_fazer(monkeypatch):
    async def sem(_db, _uid):
        return None

    monkeypatch.setattr(elevenlabs_service, "get_provider", sem)
    with pytest.raises(sound_effects.SoundUnavailable, match="Conecte a ElevenLabs"):
        await sound_effects.get_or_generate(_FakeDb(), uuid.uuid4(), "door slam")


@pytest.mark.asyncio
async def test_descricao_vazia_e_recusada(monkeypatch):
    with pytest.raises(ValueError):
        await sound_effects.get_or_generate(_FakeDb(), uuid.uuid4(), "   ...  ")
