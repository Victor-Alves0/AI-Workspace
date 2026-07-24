"""Transcrição de vídeo/áudio (media.video.transcribe).

Hermético: não usa rede, yt-dlp nem ffmpeg — as costuras de I/O externo do
transcribe_service são substituídas por fakes. Cobre o parsing de legendas, a
escolha da melhor legenda, o caminho de legendas, o fallback de fala (com STT
fake), os guardas (sem STT, duração excessiva) e o truncamento.
"""

from __future__ import annotations

import pytest

from aiworkspace.integrations import transcribe_service as ts


@pytest.fixture(autouse=True)
def _clear_cache():
    """O transcript completo é cacheado por (url, lang) — sem limpar, um teste
    herdaria o resultado do anterior (mesma URL fake) e não exercitaria nada."""
    ts._CACHE.clear()
    yield
    ts._CACHE.clear()


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


def test_subtitle_candidates_prefers_requested_lang_manual_and_format():
    info = {
        "subtitles": {"pt": [{"ext": "vtt", "url": "pt.vtt"}]},
        "automatic_captions": {
            "en": [{"ext": "vtt", "url": "en-auto.vtt"}, {"ext": "json3", "url": "en-auto.json3"}],
        },
    }
    # pediu pt: a manual pt vem primeiro
    assert ts._subtitle_candidates(info, "pt")[0] == ("pt", "vtt", "pt.vtt")
    # sem pedir: manuais antes das automáticas
    assert ts._subtitle_candidates(info, "")[0][0] == "pt"


def test_subtitle_candidates_empty_when_absent():
    assert ts._subtitle_candidates({"subtitles": {}, "automatic_captions": {}}, "en") == []


def test_subtitle_candidates_prefers_json3_over_vtt_within_lang():
    info = {"subtitles": {}, "automatic_captions": {
        "en": [{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "j"}],
    }}
    assert ts._subtitle_candidates(info, "en")[0] == ("en", "json3", "j")


def test_subtitle_candidates_puts_original_language_before_english():
    """Regressão: num vídeo em pt o YouTube expõe ~157 faixas auto-traduzidas e a de
    'en' costuma vir vazia; fixar 'en' no topo derrubava a transcrição para o STT."""
    info = {
        "subtitles": {},
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "en.json3"}],
            "pt": [{"ext": "json3", "url": "pt.json3"}],
        },
        "language": "pt",
    }
    assert ts._subtitle_candidates(info, "")[0][0] == "pt"


async def test_via_captions_falls_through_empty_track(monkeypatch):
    """Uma faixa que baixa VAZIA não pode abortar: tenta a próxima candidata."""
    info = {
        "subtitles": {},
        "automatic_captions": {
            "en": [{"ext": "vtt", "url": "empty"}],
            "pt": [{"ext": "vtt", "url": "good"}],
        },
    }

    async def fake_get(url):
        if url == "empty":
            return "WEBVTT\n\n"  # sem cues -> texto vazio
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nconteudo real\n"

    monkeypatch.setattr(ts, "_http_get_text", fake_get)
    out = await ts._via_captions(info, "en")
    assert out is not None
    assert out[0] == "conteudo real" and out[1] == "pt"


def test_clip_truncates_and_flags():
    out = ts._clip("abcdef", 3)
    assert out["text"] == "abc"
    assert out["chars"] == 3
    assert out["full_chars"] == 6
    assert out["truncated"] is True
    # a nota NÃO pode convidar a re-chamar em escadinha (o modelo fez 3000→5000→
    # 5800→5822 num turno); ela desencoraja e já dá o valor único do texto completo
    note = out["note"]
    assert "Do NOT re-call" in note and "max_chars=6" in note


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


async def test_returns_real_channel_metadata(monkeypatch):
    """O canal PRECISA vir da plataforma: sem ele o modelo inventou o nome do canal
    duas vezes ('Ciência Todo Dia', depois 'Infinity') — não está na transcrição."""
    monkeypatch.setattr(ts, "_extract_info", lambda url: {
        "title": "Provando que 1 = 2",
        "channel": "Universo Narrado",
        "uploader": "Universo Narrado",
        "duration": 345,
        "upload_date": "20240115",
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "automatic_captions": {"pt": [{"ext": "vtt", "url": "c.vtt"}]},
        "language": "pt",
    })

    async def fake_get(url):
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nfala do video\n"

    monkeypatch.setattr(ts, "_http_get_text", fake_get)
    out = await ts.transcribe_url("https://x/y", "", 12000, [])
    assert out["channel"] == "Universo Narrado"
    assert out["title"] == "Provando que 1 = 2"
    assert out["upload_date"] == "2024-01-15"
    assert out["webpage_url"].endswith("abc")
    assert out["duration"] == 345


async def test_uploader_used_when_channel_absent(monkeypatch):
    monkeypatch.setattr(ts, "_extract_info", lambda url: {
        "title": "T", "uploader": "Canal do Fulano",
        "automatic_captions": {"pt": [{"ext": "vtt", "url": "c.vtt"}]},
    })

    async def fake_get(url):
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nx\n"

    monkeypatch.setattr(ts, "_http_get_text", fake_get)
    out = await ts.transcribe_url("https://x/y", "", 12000, [])
    assert out["channel"] == "Canal do Fulano"


async def test_second_call_hits_cache_without_refetching(monkeypatch):
    """Re-chamar com max_chars maior NÃO pode re-baixar (o modelo faz isso em
    cascata); só o recorte muda."""
    calls = {"n": 0}

    def counting_info(url):
        calls["n"] += 1
        return {"title": "T", "automatic_captions": {"pt": [{"ext": "vtt", "url": "c.vtt"}]}}

    async def fake_get(url):
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + ("palavra " * 400) + "\n"

    monkeypatch.setattr(ts, "_extract_info", counting_info)
    monkeypatch.setattr(ts, "_http_get_text", fake_get)

    a = await ts.transcribe_url("https://x/y", "", 100, [])
    b = await ts.transcribe_url("https://x/y", "", 3000, [])
    assert calls["n"] == 1, "a segunda chamada re-baixou em vez de usar o cache"
    assert a["truncated"] is True and a["chars"] == 100
    assert b["chars"] > a["chars"] and b["full_chars"] == a["full_chars"]


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


# --------------------------------------------------------------------------- #
# Runner rotativo do yt-dlp (anti-bloqueio): usa yt-dlp e o pool FALSOS
# --------------------------------------------------------------------------- #
def test_run_ytdlp_rotates_identity_on_block(tmp_path, monkeypatch):
    """Com ≥2 cookies, um bloqueio (bot-check) rotaciona p/ outra identidade e
    re-tenta; um erro terminal propaga sem re-tentar."""
    import sys
    import types

    from aiworkspace.integrations import ytdlp_pool

    for i in range(2):
        (tmp_path / f"id{i}.txt").write_text("# Netscape\n")
    ytdlp_pool.reset()
    monkeypatch.setattr(
        ytdlp_pool, "get_settings",
        lambda: types.SimpleNamespace(
            transcribe_cookies_dir=str(tmp_path), transcribe_cookie_cooldown_seconds=300
        ),
    )

    seen: list[str | None] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            seen.append(self.opts.get("cookiefile"))
            if len(seen) == 1:
                raise RuntimeError("ERROR: Sign in to confirm you're not a bot")
            return {"ok": True}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))

    out = ts._run_ytdlp({"quiet": True}, lambda ydl: ydl.extract_info("u", download=False))
    assert out == {"ok": True}
    assert len(seen) == 2            # bloqueou na 1ª, rotacionou e conseguiu na 2ª
    assert seen[0] != seen[1]        # identidades diferentes
    ytdlp_pool.reset()


def test_run_ytdlp_propagates_terminal_error_without_retry(tmp_path, monkeypatch):
    import sys
    import types

    from aiworkspace.integrations import ytdlp_pool

    for i in range(2):
        (tmp_path / f"id{i}.txt").write_text("# Netscape\n")
    ytdlp_pool.reset()
    monkeypatch.setattr(
        ytdlp_pool, "get_settings",
        lambda: types.SimpleNamespace(
            transcribe_cookies_dir=str(tmp_path), transcribe_cookie_cooldown_seconds=300
        ),
    )

    calls = {"n": 0}

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            calls["n"] += 1
            raise RuntimeError("ERROR: Video unavailable: private video")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))

    with pytest.raises(RuntimeError):
        ts._run_ytdlp({"quiet": True}, lambda ydl: ydl.extract_info("u", download=False))
    assert calls["n"] == 1           # erro terminal não re-tenta
    ytdlp_pool.reset()
