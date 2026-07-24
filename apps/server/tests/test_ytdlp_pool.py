"""Pool rotativo de cookies + headers do yt-dlp (anti-bloqueio da transcrição).

Hermético: sem rede, sem yt-dlp. Cria arquivos cookies.txt de mentira num tmp_path e
verifica os headers realistas, a detecção de bloqueio, a montagem de opts sem mutar o
base, a rotação por menos-recente-uso e o cooldown com backoff.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiworkspace.integrations import ytdlp_pool


@pytest.fixture(autouse=True)
def _reset():
    ytdlp_pool.reset()
    yield
    ytdlp_pool.reset()


def _settings(directory: str = "", cooldown: int = 1800) -> SimpleNamespace:
    return SimpleNamespace(
        transcribe_cookies_dir=directory,
        transcribe_cookie_cooldown_seconds=cooldown,
    )


def _mk_cookies(dirpath, n: int) -> str:
    for i in range(n):
        (dirpath / f"id{i}.txt").write_text("# Netscape HTTP Cookie File\n")
    return str(dirpath)


# --------------------------------------------------------------------------- #
# Helpers puros
# --------------------------------------------------------------------------- #
def test_base_headers_have_realistic_ua():
    h = ytdlp_pool.base_headers()
    assert "Mozilla/5.0" in h["User-Agent"]
    assert h["Accept-Language"]


def test_looks_blocked_true_for_botcheck_and_429():
    assert ytdlp_pool.looks_blocked("ERROR: Sign in to confirm you're not a bot")
    assert ytdlp_pool.looks_blocked("HTTP Error 429: Too Many Requests")
    assert ytdlp_pool.looks_blocked(RuntimeError("who has blocked it in your country"))


def test_looks_blocked_false_for_terminal_errors():
    assert not ytdlp_pool.looks_blocked("ERROR: Video unavailable: private video")
    assert not ytdlp_pool.looks_blocked("ERROR: Unsupported URL: https://example.com")


def test_apply_evasion_adds_cookie_and_headers_without_mutating_base():
    base = {"quiet": True, "http_headers": {"X-Prev": "1"}}
    out = ytdlp_pool.apply_evasion(base, "/c/id0.txt")
    assert out["cookiefile"] == "/c/id0.txt"
    assert out["http_headers"]["X-Prev"] == "1"       # preserva header prévio
    assert "User-Agent" in out["http_headers"]
    assert "cookiefile" not in base                    # não mutou o base
    assert "User-Agent" not in base["http_headers"]    # nem os headers do base


def test_apply_evasion_without_cookie_still_adds_headers():
    out = ytdlp_pool.apply_evasion({}, None)
    assert "cookiefile" not in out
    assert "User-Agent" in out["http_headers"]


# --------------------------------------------------------------------------- #
# Rotação / cooldown
# --------------------------------------------------------------------------- #
def test_no_dir_means_no_cookie(monkeypatch):
    monkeypatch.setattr(ytdlp_pool, "get_settings", lambda: _settings(""))
    assert ytdlp_pool.acquire_cookie() is None
    assert ytdlp_pool.cookie_count() == 0


def test_rotates_least_recently_used(tmp_path, monkeypatch):
    directory = _mk_cookies(tmp_path, 3)
    monkeypatch.setattr(ytdlp_pool, "get_settings", lambda: _settings(directory))
    assert ytdlp_pool.cookie_count() == 3
    picks = [ytdlp_pool.acquire_cookie() for _ in range(3)]
    assert len(set(picks)) == 3                 # 3 identidades distintas antes de repetir
    assert ytdlp_pool.acquire_cookie() == picks[0]  # a 4ª volta p/ a menos-recente-usada


def test_blocked_cookie_is_avoided(tmp_path, monkeypatch):
    directory = _mk_cookies(tmp_path, 2)
    monkeypatch.setattr(ytdlp_pool, "get_settings", lambda: _settings(directory, cooldown=300))
    a = ytdlp_pool.acquire_cookie()
    ytdlp_pool.report_cookie(a, blocked=True)
    b = ytdlp_pool.acquire_cookie()
    assert b != a                               # evita a banida
    c = ytdlp_pool.acquire_cookie()
    assert c == b                               # a única pronta é devolvida de novo


def test_all_in_cooldown_still_returns_least_bad(tmp_path, monkeypatch):
    directory = _mk_cookies(tmp_path, 1)
    monkeypatch.setattr(ytdlp_pool, "get_settings", lambda: _settings(directory, cooldown=300))
    a = ytdlp_pool.acquire_cookie()
    ytdlp_pool.report_cookie(a, blocked=True)
    # única identidade e em cooldown → melhor devolvê-la do que nada
    assert ytdlp_pool.acquire_cookie() == a
    ytdlp_pool.report_cookie(a, blocked=False)  # sucesso limpa o estado (sem exceção)


def test_success_report_is_noop_for_unknown_path(monkeypatch):
    monkeypatch.setattr(ytdlp_pool, "get_settings", lambda: _settings("", cooldown=300))
    ytdlp_pool.report_cookie(None, blocked=True)          # não explode
    ytdlp_pool.report_cookie("/nope.txt", blocked=True)   # caminho desconhecido: no-op
