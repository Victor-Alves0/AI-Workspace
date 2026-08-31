"""Fluxos de "botão de auth" que substituem colar chave: OpenRouter (OAuth PKCE) e
GitHub (device flow).

Nenhuma rede: `httpx.AsyncClient` é trocado por um fake. O que importa aqui é o que
não dá para ver olhando a tela — que o `state` amarra o usuário certo, que o
`code_verifier` casa com o `code_challenge` enviado, e que a sondagem do device flow
distingue "ainda não" de "acabou"."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from aiworkspace.integrations import github_service as gh
from aiworkspace.integrations import openrouter_oauth as orx


# --------------------------------------------------------------------------- #
# OpenRouter — PKCE
# --------------------------------------------------------------------------- #
def test_state_carrega_o_usuario_e_expira_sozinho():
    state = orx.sign_state("user-1")
    parsed = orx.verify_state(state)
    assert parsed is not None
    assert parsed[0] == "user-1"


def test_state_adulterado_e_recusado():
    state = orx.sign_state("user-1")
    assert orx.verify_state(state[:-3] + "aaa") is None
    assert orx.verify_state("nada disso") is None


def test_state_de_outro_fluxo_e_recusado():
    """Um state legítimo de OUTRA integração não pode valer aqui: o `typ` separa."""
    from aiworkspace.integrations import github_service

    alheio = github_service.sign_state("user-1")  # typ = github_oauth
    assert orx.verify_state(alheio) is None


def test_verifier_e_derivado_e_estavel_por_jti():
    """Derivado do APP_SECRET: reconstrutível depois de um restart, e diferente por
    fluxo — é o que dispensa guardar estado em memória durante o redirect."""
    a = orx.code_verifier("jti-a")
    assert a == orx.code_verifier("jti-a")
    assert a != orx.code_verifier("jti-b")
    # formato exigido pela RFC 7636: 43–128 chars, alfabeto URL-safe
    assert 43 <= len(a) <= 128
    assert a.strip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~") == ""


def test_challenge_enviado_confere_com_o_verifier_do_state():
    """O elo do PKCE: quem receber o code sem o APP_SECRET não fecha a troca."""
    url = orx.authorization_url("user-1")
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"]

    callback = q["callback_url"][0]
    state = callback.rsplit("/", 1)[-1]  # o state viaja no CAMINHO do callback
    parsed = orx.verify_state(state)
    assert parsed is not None
    _, jti = parsed

    esperado = base64.urlsafe_b64encode(
        hashlib.sha256(orx.code_verifier(jti).encode()).digest()
    ).decode().rstrip("=")
    assert q["code_challenge"] == [esperado]


class _FakeResp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.headers = headers or {}
        self.text = str(self._data)

    def json(self):
        return self._data


class _FakeAsyncClient:
    """Troca httpx.AsyncClient: grava a última chamada e devolve o que a rota mandar."""
    last: dict = {}
    route = staticmethod(lambda url, data, json: _FakeResp())

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, data=None, json=None):
        _FakeAsyncClient.last = {"url": url, "data": data, "json": json}
        return _FakeAsyncClient.route(url, data, json)

    async def get(self, url, headers=None):
        _FakeAsyncClient.last = {"url": url}
        return _FakeAsyncClient.route(url, None, None)


def _patch(monkeypatch, modulo, route):
    _FakeAsyncClient.route = staticmethod(route)
    monkeypatch.setattr(modulo.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_troca_envia_o_verifier_e_devolve_a_chave(monkeypatch):
    _patch(monkeypatch, orx, lambda url, data, json: _FakeResp(200, {"key": "sk-or-v1-abc"}))
    out = await orx.exchange_code("code-123", "jti-x")
    assert out == {"key": "sk-or-v1-abc"}
    enviado = _FakeAsyncClient.last["json"]
    assert enviado["code"] == "code-123"
    assert enviado["code_verifier"] == orx.code_verifier("jti-x")


@pytest.mark.asyncio
async def test_troca_sem_chave_na_resposta_vira_erro(monkeypatch):
    """200 com corpo vazio não pode virar uma chave vazia salva no banco."""
    _patch(monkeypatch, orx, lambda url, data, json: _FakeResp(200, {}))
    assert (await orx.exchange_code("code", "jti"))["error"] == "empty_key"


@pytest.mark.asyncio
async def test_troca_com_erro_http_nao_vira_chave(monkeypatch):
    _patch(monkeypatch, orx, lambda url, data, json: _FakeResp(400, {"error": "bad code"}))
    assert "error" in await orx.exchange_code("code", "jti")


# --------------------------------------------------------------------------- #
# GitHub — device flow
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_device_start_pede_os_escopos_sem_secret(monkeypatch):
    _patch(monkeypatch, gh, lambda url, data, json: _FakeResp(200, {
        "device_code": "dc", "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "interval": 5, "expires_in": 900,
    }))
    out = await gh.device_start("Iv1.public")
    assert out["user_code"] == "ABCD-1234"
    enviado = _FakeAsyncClient.last["data"]
    assert enviado["client_id"] == "Iv1.public"
    assert "client_secret" not in enviado  # o device flow não tem segredo


@pytest.mark.asyncio
async def test_device_start_propaga_o_motivo_da_recusa(monkeypatch):
    """Erro típico: device flow desligado no OAuth App — precisa chegar na tela."""
    _patch(monkeypatch, gh, lambda url, data, json: _FakeResp(200, {
        "error": "device_flow_disabled",
        "error_description": "Device flow is not enabled for this app",
    }))
    assert "not enabled" in (await gh.device_start("Iv1.x"))["error"]


@pytest.mark.asyncio
async def test_device_poll_pendente_nao_encerra(monkeypatch):
    _patch(monkeypatch, gh, lambda url, data, json: _FakeResp(200, {"error": "authorization_pending"}))
    assert (await gh.device_poll("cid", "dc")) == {"status": "pending"}


@pytest.mark.asyncio
async def test_device_poll_slow_down_devolve_o_novo_intervalo(monkeypatch):
    _patch(monkeypatch, gh, lambda url, data, json: _FakeResp(200, {"error": "slow_down", "interval": 12}))
    assert (await gh.device_poll("cid", "dc")) == {"status": "slow_down", "interval": 12}


@pytest.mark.asyncio
async def test_device_poll_expirado_e_terminal(monkeypatch):
    _patch(monkeypatch, gh, lambda url, data, json: _FakeResp(200, {
        "error": "expired_token", "error_description": "expired"
    }))
    assert (await gh.device_poll("cid", "dc"))["status"] == "error"


@pytest.mark.asyncio
async def test_device_poll_sucesso_resolve_a_conta(monkeypatch):
    def route(url, data, json):
        if url.endswith("/login/oauth/access_token"):
            return _FakeResp(200, {"access_token": "gho_x", "scope": "repo,read:user"})
        return _FakeResp(200, {"login": "victor", "avatar_url": "http://a/b.png"})

    _patch(monkeypatch, gh, route)
    out = await gh.device_poll("cid", "dc")
    assert out["status"] == "ok"
    assert out["login"] == "victor"
    assert out["access_token"] == "gho_x"


@pytest.mark.asyncio
async def test_device_poll_sem_token_no_200_e_erro(monkeypatch):
    _patch(monkeypatch, gh, lambda url, data, json: _FakeResp(200, {}))
    assert (await gh.device_poll("cid", "dc"))["status"] == "error"
