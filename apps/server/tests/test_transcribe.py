"""Transcrição de vídeo/áudio (media.video.transcribe).

Hermético: não usa rede, yt-dlp nem ffmpeg — as costuras de I/O externo do
transcribe_service são substituídas por fakes. Cobre o parsing de legendas, a
escolha da melhor legenda, o caminho de legendas, o fallback de fala (com STT
fake), os guardas (sem STT, duração excessiva) e o truncamento.
"""

from __future__ import annotations

import pytest

from aiworkspace.integrations import transcribe_service as ts


# --------------------------------------------------------------------------- #
# Parsing de legendas
# --------------------------------------------------------------------------- #
def test_vtt_to_text_strips_header_timestamps_and_dedup():
    vtt = (
        "WEBVTT\n\n"
        "1\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "Hello <c>world</c>\n\n"
        "2\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "Hello world\n"          # duplicata rolante -> deduplicada
        "This is a test\n"
    )
    out = ts._vtt_to_text(vtt)
    assert out == "Hello world This is a test"


def test_json3_to_text_concats_segs():
    data = {"events": [
        {"segs": [{"utf8": "Hello"}, {"utf8": " "}, {"utf8": "world"}]},
        {"segs": [{"utf8": "\n"}]},
        {"segs": [{"utf8": "again"}]},
    ]}
    assert ts._json3_to_text(data) == "Hello world again"


def test_pick_subtitle_prefers_requested_lang_manual_and_format():
    info = {
        "subtitles": {"pt": [{"ext": "vtt", "url": "pt.vtt"}]},
        "automatic_captions": {
            "en": [{"ext": "vtt", "url": "en-auto.vtt"}, {"ext": "json3", "url": "en-auto.json3"}],
        },
    }
    # pediu pt: pega a manual pt
    assert ts._pick_subtitle(info, "pt") == ("pt", "vtt", "pt.vtt")
    # sem pedir: manual (pt) vem antes das automáticas
    assert ts._pick_subtitle(info, "")[0] == "pt"


def test_pick_subtitle_none_when_absent():
    assert ts._pick_subtitle({"subtitles": {}, "automatic_captions": {}}, "en") is None


def test_pick_subtitle_prefers_json3_over_vtt_within_lang():
    info = {"subtitles": {}, "automatic_captions": {
        "en": [{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "j"}],
    }}
    assert ts._pick_subtitle(info, "en") == ("en", "json3", "j")


def test_clip_truncates_and_flags():
    out = ts._clip("abcdef", 3)
    assert out["text"] == "abc"
    assert out["chars"] == 3
    assert out["full_chars"] == 6
    assert out["truncated"] is True
    assert "truncated" in out["note"].lower()


def test_clip_no_truncate_when_short():
    out = ts._clip("abc", 10)
    assert out["text"] == "abc" and out.get("truncated") is None


# --------------------------------------------------------------------------- #
# transcribe_url — caminho de legendas
# --------------------------------------------------------------------------- #
async def test_transcribe_url_captions_path(monkeypatch):
    monkeypatch.setattr(ts, "_extract_info", lambda url: {
        "title": "T", "duration": 42,
        "automatic_captions": {"en": [{"ext": "vtt", "url": "cap.vtt"}]},
    })

    async def fake_get(url):
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello there\n"

    monkeypatch.setattr(ts, "_http_get_text", fake_get)
    out = await ts.transcribe_url("https://x/y", "", 12000, [])
    assert out["ok"] and out["source"] == "captions"
    assert out["title"] == "T" and out["lang"] == "en" and out["duration"] == 42
    assert out["text"] == "hello there"


async def test_transcribe_url_falls_back_to_speech_when_no_captions(monkeypatch):
    monkeypatch.setattr(ts, "_extract_info", lambda url: {"title": "T", "duration": 10})
    monkeypatch.setattr(ts, "_download_audio", lambda url, tmp: "/tmp/audio.webm")
    monkeypatch.setattr(ts, "_segment_audio", lambda path, tmp: ["/tmp/seg000.mp3"])
    monkeypatch.setattr(ts, "_read_capped", lambda path: b"AUDIO")

    async def fake_stt(cand, filename, audio):
        assert audio == b"AUDIO"
        return "spoken words"

    monkeypatch.setattr(ts, "_stt_one", fake_stt)
    cands = [{"base_url": "http://stt", "api_key": "k", "model": "whisper-1"}]
    out = await ts.transcribe_url("https://x/y", "", 12000, cands)
    assert out["ok"] and out["source"] == "speech"
    assert out["text"] == "spoken words"


async def test_transcribe_url_speech_needs_stt(monkeypatch):
    monkeypatch.setattr(ts, "_extract_info", lambda url: {"title": "T", "duration": 10})
    out = await ts.transcribe_url("https://x/y", "", 12000, [])  # sem candidatos STT
    assert "error" in out and "STT" in out["error"]


async def test_transcribe_url_rejects_overlong_speech(monkeypatch):
    monkeypatch.setattr(ts, "_extract_info", lambda url: {
        "title": "T", "duration": ts._MAX_SPEECH_DURATION + 1,
    })
    cands = [{"base_url": "http://stt", "api_key": "k", "model": "whisper-1"}]
    out = await ts.transcribe_url("https://x/y", "", 12000, cands)
    assert "error" in out and "longa" in out["error"]


async def test_transcribe_url_stt_all_unavailable(monkeypatch):
    monkeypatch.setattr(ts, "_extract_info", lambda url: {"title": "T", "duration": 10})
    monkeypatch.setattr(ts, "_download_audio", lambda url, tmp: "/tmp/audio.webm")
    monkeypatch.setattr(ts, "_segment_audio", lambda path, tmp: ["/tmp/seg000.mp3"])
    monkeypatch.setattr(ts, "_read_capped", lambda path: b"AUDIO")

    async def fake_stt(cand, filename, audio):
        return None  # todos os servidores recusam STT (404)

    monkeypatch.setattr(ts, "_stt_one", fake_stt)
    cands = [{"base_url": "http://stt", "api_key": "k", "model": "whisper-1"}]
    out = await ts.transcribe_url("https://x/y", "", 12000, cands)
    assert "error" in out and "transcri" in out["error"].lower()


async def test_transcribe_url_extract_failure_is_friendly(monkeypatch):
    def boom(url):
        raise RuntimeError("Unsupported URL: whatever")

    monkeypatch.setattr(ts, "_extract_info", boom)
    out = await ts.transcribe_url("https://x/y", "", 12000, [])
    assert "error" in out and "não" in out["error"].lower()


async def test_transcribe_url_captions_truncated(monkeypatch):
    long = " ".join(f"w{i}" for i in range(5000))
    monkeypatch.setattr(ts, "_extract_info", lambda url: {
        "title": "T", "duration": 1,
        "subtitles": {"en": [{"ext": "vtt", "url": "c.vtt"}]},
    })

    async def fake_get(url):
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + long + "\n"

    monkeypatch.setattr(ts, "_http_get_text", fake_get)
    out = await ts.transcribe_url("https://x/y", "", 1000, [])
    assert out["ok"] and out["truncated"] is True and out["chars"] == 1000


# --------------------------------------------------------------------------- #
# Registro / guarda anti-SSRF na tool
# --------------------------------------------------------------------------- #
def test_tool_is_registered_as_native():
    from aiworkspace.tools import sift_service

    paths = {t["path"] for t in sift_service.BUILTIN_TOOLS}
    assert "media.video.transcribe" in paths
    cat = sift_service.tool_category("media.video.transcribe")
    assert cat["category"] == "native"


@pytest.mark.parametrize("bad", ["http://db:5432", "http://localhost/x", "http://192.168.1.5/v"])
def test_internal_urls_blocked_by_guard(bad):
    from aiworkspace.tools import sift_service

    assert sift_service._public_web_url(bad) is False
