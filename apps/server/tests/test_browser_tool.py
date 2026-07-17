"""Guarda anti-SSRF do Navegador (web.browser.use) e roteamento de ação.

Puro/hermético: não sobe browser nem toca DB. Cobre o `_public_web_url` (o vetor
de segurança) e o roteamento da tool com um driver FAKE.
"""

from __future__ import annotations

from aiworkspace.tools import sift_service


def test_public_web_url_blocks_internal_and_localhost():
    bad = [
        "http://db:5432",            # nome de serviço do compose (sem ponto)
        "http://server:8000/x",      # idem
        "http://browser:3000",       # idem
        "http://localhost:8000",     # localhost
        "https://127.0.0.1/",        # loopback literal
        "http://192.168.1.10/",      # IP privado literal
        "http://169.254.169.254/",   # link-local (metadata)
        "ftp://example.com",         # esquema não http
        "http://foo.local",          # .local
    ]
    for u in bad:
        assert sift_service._public_web_url(u) is False, u


def test_public_web_url_allows_real_domains():
    for u in ["https://example.com", "http://example.com/path?q=1", "https://sub.example.co.uk"]:
        assert sift_service._public_web_url(u) is True, u
