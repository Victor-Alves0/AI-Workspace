"""Fixtures globais da bateria."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _zera_limite_de_login():
    """O limite de tentativas de login é memória do processo: sem zerar, os logins
    dos testes anteriores (mesmo IP de TestClient, mesmo e-mail) somavam e um teste
    qualquer tomava 429 dependendo da ordem da bateria."""
    from aiworkspace.auth import ratelimit

    with ratelimit._lock:
        ratelimit._hits.clear()
    yield
