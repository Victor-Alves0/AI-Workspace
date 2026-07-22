"""Vídeos no Conhecimento (detecção/indexação + Range no /raw) e resolução do
endpoint do Navegador por-usuário (config → env fallback)."""
from __future__ import annotations

from aiworkspace.knowledge import ingest
from aiworkspace.knowledge_routes import _parse_range
from aiworkspace.tools import sift_service


# ------------------------------- vídeo ------------------------------------- #
def test_is_video_by_mime_and_ext():
    assert ingest.is_video("clip.mp4", "")
    assert ingest.is_video("x", "video/webm")
    assert ingest.is_video("a.MKV", "")
    assert not ingest.is_video("foto.png", "image/png")
    assert not ingest.is_video("doc.pdf", "application/pdf")


def test_video_text_from_filename():
    t = ingest.extract_text("minha-viagem-2024.mp4", "video/mp4", b"\x00\x01")
    assert t.startswith("Vídeo:")
    assert "viagem" in t
    # bytes NUNCA são decodificados (evita lixo binário no índice)
    assert "\x00" not in t


# ------------------------------- Range ------------------------------------- #
def test_parse_range_basic():
    assert _parse_range("bytes=0-99", 1000) == (0, 99)
    assert _parse_range("bytes=100-", 1000) == (100, 999)   # fim aberto
    assert _parse_range("bytes=-100", 1000) == (900, 999)   # sufixo (últimos N)


def test_parse_range_clamps_and_rejects():
    assert _parse_range("bytes=0-99999", 1000) == (0, 999)  # fim além do tamanho → clamp
    assert _parse_range("", 1000) is None                   # sem header
    assert _parse_range("bytes=2000-3000", 1000) is None    # início além do tamanho
    assert _parse_range("bytes=abc", 1000) is None          # inválido


# --------------------------- endpoint do browser --------------------------- #
def test_browser_endpoint_user_config_wins():
    ep = sift_service._browser_endpoint({"ws_url": "ws://host:9", "token": "T", "enabled": True})
    assert ep == "ws://host:9?token=T"


def test_browser_endpoint_no_token():
    assert sift_service._browser_endpoint({"ws_url": "ws://host:9"}) == "ws://host:9"


def test_browser_endpoint_disabled_returns_empty():
    assert sift_service._browser_endpoint({"ws_url": "ws://host:9", "enabled": False}) == ""


def test_browser_endpoint_env_fallback(monkeypatch):
    # sem ws_url na config → cai no env (get_settings)
    from aiworkspace import config
    s = config.get_settings()
    monkeypatch.setattr(s, "browser_ws_url", "ws://envhost:3000", raising=False)
    monkeypatch.setattr(s, "browser_token", "envtok", raising=False)
    assert sift_service._browser_endpoint({}) == "ws://envhost:3000?token=envtok"
    assert sift_service._browser_endpoint(None) == "ws://envhost:3000?token=envtok"
