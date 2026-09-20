"""CORS nas respostas de ERRO e o `Secure` do cookie de sessão.

Caso real (20/09): upload falhava e o navegador só dizia "bloqueado por CORS — sem
Access-Control-Allow-Origin". O erro de verdade (criado pelo middleware de segurança,
que ficava POR FORA do CORS) chegava sem os cabeçalhos, escondendo a causa. E o cookie
marcado `Secure` por causa do WEB_ORIGIN https era DESCARTADO em acesso http pela
LAN/VPN — tudo respondia 401 como se a sessão tivesse expirado.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aiworkspace import main, network_config
from aiworkspace.auth import routes as auth_routes

ORIGEM = "http://10.10.0.10:3000"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.get_settings().__class__, "cors_origins",
                        property(lambda self: [ORIGEM]))
    # sem `with`: não dispara o lifespan (conexões, pre-warm) — aqui só interessam
    # os middlewares e os cabeçalhos da resposta
    return TestClient(main.create_app())


def test_cors_vem_na_resposta_normal(client):
    r = client.get("/health", headers={"Origin": ORIGEM})
    assert r.status_code == 200


def test_erro_do_middleware_de_seguranca_chega_com_cors(client, monkeypatch):
    """IP bloqueado: o navegador precisa LER o 403 — senão mostra "erro de CORS"."""
    monkeypatch.setattr(network_config, "is_allowed", lambda ip: False)
    r = client.get("/chats", headers={"Origin": ORIGEM})
    assert r.status_code == 403
    assert r.headers.get("access-control-allow-origin") == ORIGEM


def test_o_cors_e_a_camada_mais_externa():
    """Em Starlette o ÚLTIMO middleware adicionado é o mais externo; o CORS precisa
    estar lá para envolver também as respostas criadas pelos outros middlewares."""
    app = main.create_app()
    assert app.user_middleware[0].cls.__name__ == "CORSMiddleware"


# --------------------------------------------------------------------------- #
# Cookie                                                                       #
# --------------------------------------------------------------------------- #
class _Req:
    def __init__(self, scheme: str, headers: dict | None = None):
        self.url = type("U", (), {"scheme": scheme})()
        self.headers = headers or {}


def test_cookie_secure_segue_o_acesso_e_nao_o_web_origin():
    assert auth_routes._is_https(_Req("https")) is True
    assert auth_routes._is_https(_Req("http")) is False
    # atrás de proxy: quem manda é o X-Forwarded-Proto
    assert auth_routes._is_https(_Req("http", {"x-forwarded-proto": "https"})) is True
    assert auth_routes._is_https(_Req("http", {"x-forwarded-proto": "https, http"})) is True


# --------------------------------------------------------------------------- #
# Quais origens são aceitas sem ninguém editar o .env                          #
# --------------------------------------------------------------------------- #
def _regex(app_env: str):
    import re

    from aiworkspace.config import Settings

    return re.compile(Settings(app_env=app_env).cors_origin_regex)


@pytest.mark.parametrize("origem,aceita", [
    ("https://10.10.0.10", True),            # IP da VPN, pelo proxy
    ("https://192.168.1.203", True),         # IP da LAN
    ("https://10.10.0.10:8443", True),       # proxy em outra porta
    ("https://localhost", True),
    ("http://10.10.0.10:3000", False),       # HTTP direto: caminho aposentado
    ("https://meusite.com", False),          # público exige WEB_ORIGIN explícito
    ("https://8.8.8.8", False),              # IP público não é "a casa do usuário"
])
def test_em_producao_so_a_rede_local_em_https_entra_sozinha(origem, aceita):
    """Self-hosted acessado ora pela LAN, ora pela VPN: exigir que o dono liste cada
    IP no WEB_ORIGIN só produzia "bloqueado por CORS" sem explicação."""
    assert bool(_regex("production").match(origem)) is aceita


def test_em_desenvolvimento_a_lan_em_http_continua_valendo():
    assert _regex("development").match("http://192.168.1.203:3000")
