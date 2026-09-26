"""Áudio: qualquer formato é reconhecido; ao ir direto ao modelo, o incomum vira WAV."""
from aiworkspace import uploads_service as u
from aiworkspace.chat import orchestrator as o


def test_audio_reconhecido_pela_extensao_sem_mime():
    for nome in ("voz.opus", "x.amr", "y.wma", "nota.m4a", "gravacao.webm", "a.flac", "b.aiff"):
        assert u.kind_for(nome, "") == "audio", nome
    assert u.kind_for("doc.pdf", "") == "file"
    assert u.kind_for("qualquer", "audio/x-custom") == "audio"


def test_formato_comum_vai_como_esta():
    part = o._audio_part({"url": "data:audio/mpeg;base64,AAAA"})
    assert part == {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "mp3"}}


def test_formato_incomum_vira_wav(monkeypatch):
    monkeypatch.setattr(o, "_to_wav_ffmpeg", lambda raw: b"RIFFwav")
    part = o._audio_part({"url": "data:audio/webm;base64,AAAA"})
    assert part["input_audio"]["format"] == "wav"


def test_sem_conversor_manda_o_original(monkeypatch):
    monkeypatch.setattr(o, "_to_wav_ffmpeg", lambda raw: None)
    assert o._audio_part({"url": "data:audio/ogg;codecs=opus;base64,AAAA"})["input_audio"]["format"] == "ogg"
    assert o._audio_part({"url": "data:audio/webm;base64,AAAA"})["input_audio"]["format"] == "webm"
