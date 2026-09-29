"""Foto antiga + troca para modelo sem visão: o chat não pode travar.

Bug: o usuário mandou uma foto a um modelo com visão e trocou para um modelo que não
aceita imagens; a foto do histórico ia como `image_url` e o provedor recusava TODO
turno seguinte. Agora a mídia que o modelo do turno não aceita vira texto em todas as
mensagens, e uma recusa do provedor por causa da mídia refaz UMA vez sem ela.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from aiworkspace.chat import attachment_context as ac
from aiworkspace.chat import media_support as ms
from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import MediaOpts, TurnSession, run_turn
from aiworkspace.providers import openrouter

IMG = "data:image/png;base64,AAAA"
ERRO_IMAGEM = 'OpenRouter HTTP 404: {"error":{"message":"No endpoints found that support image input","code":404}}'


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    ms.forget_rejections()
    monkeypatch.setattr(openrouter, "_catalog_cache", {})
    yield
    ms.forget_rejections()


def _m(role, content, atts=None):
    return SimpleNamespace(role=role, content=content, attachments=atts, compacted=False, reasoning=None)


def _texto(c="ok"):
    return {"choices": [{"delta": {"content": c}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}


def _partes(messages, tipo):
    return [p for m in messages if isinstance(m.get("content"), list)
            for p in m["content"] if isinstance(p, dict) and p.get("type") == tipo]


async def _rodar(monkeypatch, fake, *, history, media, user_text="e agora?", model="m/texto"):
    monkeypatch.setattr(orch.openrouter, "stream_chat", fake)
    return [ev async for ev in run_turn(
        api_key="k", model=model, history=history, user_text=user_text,
        chat_system_prompt=None, params={}, use_tools=False,
        session=TurnSession(user_id="u"), media=media,
    )]


def _gravador(respostas):
    """stream_chat falso: cada chamada consome uma resposta (exceção ou texto)."""
    chamadas: list[list[dict]] = []

    async def fake(api_key, model, messages, **kw):
        chamadas.append([dict(m) for m in messages])
        r = respostas[min(len(chamadas) - 1, len(respostas) - 1)]
        if isinstance(r, Exception):
            raise r
        yield _texto(r)

    return fake, chamadas


# --------------------------------------------------------------------------- #
# Histórico                                                                   #
# --------------------------------------------------------------------------- #
async def test_modelo_sem_visao_recebe_aviso_em_texto_no_lugar_da_foto_antiga():
    h = await ac.history([
        _m("user", "olha", [{"type": "image", "name": "foto.jpg", "url": IMG}]),
        _m("assistant", "Um gato."),
    ])

    sem = ac.for_provider(h, vision=False)
    assert isinstance(sem[0]["content"], str)
    assert "[imagem anexada: foto.jpg — este modelo não vê imagens]" in sem[0]["content"]

    com = ac.for_provider(h, vision=True)
    assert com[0]["content"][1] == {"type": "image_url", "image_url": {"url": IMG}}
    assert "[Imagem anexada: foto.jpg]" in com[0]["content"][0]["text"]


async def test_troca_para_modelo_sem_visao_nao_manda_nenhuma_imagem(monkeypatch):
    h = await ac.history([
        _m("user", "olha", [{"type": "image", "name": "foto.jpg", "url": IMG}]),
        _m("assistant", "Um gato."),
    ])
    # histórico cru (ex.: API pública) com a imagem já como parte
    h.append({"role": "user", "content": [{"type": "text", "text": "e esta?"},
                                          {"type": "image_url", "image_url": {"url": IMG}}]})
    h.append({"role": "assistant", "content": "Outro gato."})
    fake, chamadas = _gravador(["respondi"])

    evs = await _rodar(monkeypatch, fake, history=h, media=MediaOpts(vision=False))

    assert len(chamadas) == 1
    assert not _partes(chamadas[0], "image_url")
    enviado = "\n".join(str(m["content"]) for m in chamadas[0])
    assert "este modelo não vê imagens" in enviado
    assert any(e.get("type") == "done" for e in evs)


async def test_modelo_com_visao_continua_recebendo_as_imagens(monkeypatch):
    h = await ac.history([_m("user", "olha", [{"type": "image", "name": "foto.jpg", "url": IMG}])])
    fake, chamadas = _gravador(["vi"])

    await _rodar(monkeypatch, fake, history=h, media=MediaOpts(vision=True), model="m/visao")

    assert _partes(chamadas[0], "image_url") == [{"type": "image_url", "image_url": {"url": IMG}}]


async def test_foto_nova_para_modelo_sem_visao_degrada_em_vez_de_quebrar(monkeypatch):
    fake, chamadas = _gravador(["ok"])

    evs = await _rodar(monkeypatch, fake, history=[], media=MediaOpts(
        vision=False, attachments=[{"type": "image", "name": "x.png", "url": IMG}]))

    assert not _partes(chamadas[0], "image_url")
    assert "não tem Visão" in chamadas[0][-1]["content"]
    assert not any(e.get("type") == "error" for e in evs)


# --------------------------------------------------------------------------- #
# Recusa do provedor → retry sem mídia                                        #
# --------------------------------------------------------------------------- #
async def test_recusa_por_imagem_refaz_uma_vez_sem_midia_e_memoriza(monkeypatch):
    h = await ac.history([_m("user", "olha", [{"type": "image", "name": "foto.jpg", "url": IMG}])])
    fake, chamadas = _gravador([RuntimeError(ERRO_IMAGEM), "respondi sem a foto"])

    # capacidade "Visão" marcada num modelo que na verdade não vê imagens
    evs = await _rodar(monkeypatch, fake, history=h, media=MediaOpts(vision=True))

    assert len(chamadas) == 2
    assert _partes(chamadas[0], "image_url") and not _partes(chamadas[1], "image_url")
    assert ms.IMAGE_PLACEHOLDER in "\n".join(str(m["content"]) for m in chamadas[1])
    assert not any(e.get("type") == "error" for e in evs)
    assert next(e for e in evs if e.get("type") == "done")["content"] == "respondi sem a foto"

    # próximo turno do mesmo modelo já sai sem a imagem (sem pagar a recusa de novo)
    fake2, chamadas2 = _gravador(["direto"])
    await _rodar(monkeypatch, fake2, history=h, media=MediaOpts(vision=True))
    assert len(chamadas2) == 1 and not _partes(chamadas2[0], "image_url")


async def test_recusa_repetida_nao_entra_em_loop(monkeypatch):
    h = await ac.history([_m("user", "olha", [{"type": "image", "name": "f.jpg", "url": IMG}])])
    fake, chamadas = _gravador([RuntimeError(ERRO_IMAGEM)])

    evs = await _rodar(monkeypatch, fake, history=h, media=MediaOpts(vision=True))

    # a original + UM retry sem mídia (+ no máximo o retry sem knobs, que já existia)
    assert 2 <= len(chamadas) <= 3
    assert sum(1 for c in chamadas if _partes(c, "image_url")) == 1
    assert any(e.get("type") == "error" for e in evs)


async def test_erro_que_nao_e_de_midia_nao_tira_as_imagens(monkeypatch):
    h = await ac.history([_m("user", "olha", [{"type": "image", "name": "f.jpg", "url": IMG}])])
    fake, chamadas = _gravador([RuntimeError("OpenRouter HTTP 429: rate limited")])

    evs = await _rodar(monkeypatch, fake, history=h, media=MediaOpts(vision=True))

    # (o retry sem knobs opcionais pode acontecer; a imagem continua em todos)
    assert all(_partes(c, "image_url") for c in chamadas)
    assert any(e.get("type") == "error" for e in evs)
    assert ms.accepts("m/texto", "image")


async def test_recusa_por_audio_refaz_sem_o_audio(monkeypatch):
    fake, chamadas = _gravador([
        RuntimeError("OpenRouter HTTP 404: No endpoints found that support input audio"), "ouvi não"])
    audio = {"type": "audio", "name": "a.mp3", "url": "data:audio/mpeg;base64,AAAA"}

    evs = await _rodar(monkeypatch, fake, history=[], media=MediaOpts(audio=True, attachments=[audio]))

    assert _partes(chamadas[0], "input_audio") and not _partes(chamadas[1], "input_audio")
    assert ms.AUDIO_PLACEHOLDER in str(chamadas[1][-1]["content"])
    assert not any(e.get("type") == "error" for e in evs)
    assert not ms.accepts("m/texto", "audio") and ms.accepts("m/texto", "image")


# --------------------------------------------------------------------------- #
# Catálogo e detecção                                                         #
# --------------------------------------------------------------------------- #
async def test_catalogo_em_cache_que_diz_so_texto_desliga_a_visao(monkeypatch):
    monkeypatch.setattr(openrouter, "_catalog_cache", {"all": (time.monotonic(), [
        {"id": "m/texto", "architecture": {"input_modalities": ["text"]}},
        {"id": "m/visao", "architecture": {"input_modalities": ["text", "image"]}},
    ])})
    assert not ms.accepts("m/texto", "image")
    assert ms.accepts("m/visao", "image")
    assert ms.accepts("desconhecido/x", "image")  # fora do catálogo: tenta

    h = await ac.history([_m("user", "olha", [{"type": "image", "name": "f.jpg", "url": IMG}])])
    fake, chamadas = _gravador(["ok"])
    await _rodar(monkeypatch, fake, history=h, media=MediaOpts(vision=True))
    assert len(chamadas) == 1 and not _partes(chamadas[0], "image_url")


def test_deteccao_de_recusa_de_midia():
    assert ms.rejected_kinds(ERRO_IMAGEM) == {"image"}
    assert ms.rejected_kinds("model does not support images") == {"image"}
    assert ms.rejected_kinds("Invalid content type. image_url is only supported by certain models.") == {"image"}
    assert ms.rejected_kinds("No endpoints found that support input audio") == {"audio"}
    # geração nativa de imagem é outro caso (retry sem knobs), não mídia de entrada
    assert ms.rejected_kinds("model does not support image output") == set()
    assert ms.rejected_kinds("rate limited") == set()


def test_strip_media_troca_as_partes_e_nao_muta_o_original():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "oi"},
                                         {"type": "image_url", "image_url": {"url": IMG}}]}]
    out = ms.strip_media(msgs, {"image"})
    assert out[0]["content"] == f"oi\n\n{ms.IMAGE_PLACEHOLDER}"
    assert isinstance(msgs[0]["content"], list)
    assert ms.strip_media(msgs, {"audio"}) is msgs
