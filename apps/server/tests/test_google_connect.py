"""Conexão do Google sem o usuário tocar no Google Cloud + escolha de conta pela IA.

Trava o que faz o "Conectar agora" funcionar em qualquer instalação:
  - o retorno vai para a página do projeto, que lê `ret`/`cb` do `state`;
  - PKCE: o `code` que passa pela página não vale nada sem o verificador, que só a
    instalação sabe derivar;
  - app próprio antigo (retorno direto) continua como estava;
  - cada conta renova com o app que a emitiu.
E, na tool, a principal no lugar de "qual conta?" e o fallback só quando ligado e
quando o pedido não nomeou a conta.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from aiworkspace.integrations import google_service as gs

ROOT = pathlib.Path(__file__).resolve().parents[3]
CREDS = {"client_id": "cid.apps.googleusercontent.com", "client_secret": "s3", "redirect_uri": "https://relay.example/oauth/"}


def _payload(jwt_str: str) -> dict:
    part = jwt_str.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_url_de_autorizacao_leva_pkce_retorno_e_seletor_de_conta():
    url = gs.authorization_url("u1", CREDS, nonce="n1", ret="http://192.168.1.203:8000")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["redirect_uri"] == CREDS["redirect_uri"]
    assert "select_account" in q["prompt"] and "consent" in q["prompt"]
    assert q["code_challenge_method"] == "S256"
    verif = gs.pkce_verifier("n1")
    esperado = base64.urlsafe_b64encode(hashlib.sha256(verif.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == esperado
    st = _payload(q["state"])
    assert st["ret"] == "http://192.168.1.203:8000" and st["cb"] == gs.CALLBACK_PATH and st["n"] == "n1"
    assert gs.verify_state(q["state"])["sub"] == "u1"


def test_state_adulterado_e_recusado():
    st = gs.sign_state("u1", "n1", "http://localhost:8000")
    assert gs.verify_state(st[:-3] + "abc") is None
    assert gs.verify_state("lixo") is None


def test_verificador_pkce_e_secreto_e_por_tentativa():
    a, b = gs.pkce_verifier("n1"), gs.pkce_verifier("n2")
    assert a != b and len(a) >= 43
    assert "n1" not in a  # derivado (HMAC), não o nonce em claro


def test_troca_do_codigo_manda_o_verificador(monkeypatch):
    enviados: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/token":
            enviados.append({k: v[0] for k, v in parse_qs(req.content.decode()).items()})
            return httpx.Response(200, json={"refresh_token": "r", "access_token": "a", "scope": "x"})
        return httpx.Response(200, json={"email": "eu@gmail.com"})

    real = httpx.AsyncClient
    monkeypatch.setattr(gs.httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    import asyncio
    out = asyncio.run(gs.exchange_code("c0de", CREDS, "n1"))
    assert out == {"refresh_token": "r", "email": "eu@gmail.com", "scopes": "x"}
    assert enviados[0]["code_verifier"] == gs.pkce_verifier("n1")


def test_refresh_recusado_vira_excecao_propria(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(gs.httpx, "AsyncClient", lambda *a, **k: real(
        *a, transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"})), **k))
    import asyncio
    with pytest.raises(gs.RefreshRejected):
        asyncio.run(gs._refresh_access_token("r", CREDS))


def test_tentativas_so_o_dono_ve():
    n = gs.new_attempt("u1")
    assert gs.attempt(n, "u1")["status"] == "pending"
    assert gs.attempt(n, "u2") is None
    gs.finish_attempt(n, status="ok", email="eu@gmail.com")
    assert gs.attempt(n, "u1")["email"] == "eu@gmail.com"


# --------------------------------------------------------------------------- #
# App próprio × embutido
# --------------------------------------------------------------------------- #
@pytest.fixture()
def settings_store(monkeypatch):
    store: dict = {}

    async def get_setting(db, key):
        return store.get(key)

    async def set_setting(db, key, value):
        store[key] = value

    monkeypatch.setattr(gs, "get_setting", get_setting)
    monkeypatch.setattr(gs, "set_setting", set_setting)
    return store


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_app_proprio_volta_direto_para_a_instalacao(settings_store, monkeypatch):
    """App próprio (o caminho do OpenClaw/Hermes): retorno direto em GOOGLE_REDIRECT_URI,
    salvo antes ou depois — nunca a página do projeto, que só serve ao app embutido."""
    from aiworkspace import crypto
    direto = gs.get_settings().google_redirect_uri
    settings_store[gs.OAUTH_SETTING_KEY] = {"client_id": "velho", "client_secret_enc": crypto.encrypt("s")}
    assert _run(gs.own_app(None))["redirect_uri"] == direto
    _run(gs.set_oauth_config(None, "velho", "novo"))
    assert _run(gs.own_app(None))["redirect_uri"] == direto
    _run(gs.set_oauth_config(None, "outro", "s2"))
    assert _run(gs.own_app(None))["redirect_uri"] == direto


def test_embutido_so_quando_ha_credenciais(settings_store, monkeypatch):
    monkeypatch.setattr(gs, "_BUILTIN_CLIENT_ID", "")
    monkeypatch.setattr(gs.get_settings(), "google_app_client_id", "")
    assert gs.builtin_app() is None
    monkeypatch.setattr(gs, "_BUILTIN_CLIENT_ID", "emb")
    monkeypatch.setattr(gs, "_BUILTIN_CLIENT_SECRET", "x")
    assert gs.builtin_app()["redirect_uri"] == gs.relay_uri()
    assert _run(gs.get_oauth_config(None))["source"] == "builtin"


def test_conta_renova_com_o_app_que_a_emitiu(settings_store, monkeypatch):
    from aiworkspace import crypto
    monkeypatch.setattr(gs, "_BUILTIN_CLIENT_ID", "emb")
    monkeypatch.setattr(gs, "_BUILTIN_CLIENT_SECRET", "x")
    settings_store[gs.OAUTH_SETTING_KEY] = {"client_id": "meu", "client_secret_enc": crypto.encrypt("s"), "redirect_uri": ""}
    assert _run(gs.creds_for_client(None, "emb"))["client_id"] == "emb"
    assert _run(gs.creds_for_client(None, "meu"))["client_id"] == "meu"
    assert _run(gs.creds_for_client(None, ""))["client_id"] == "meu"  # conta antiga → app em vigor


# --------------------------------------------------------------------------- #
# Página de retorno (site/oauth) aceita o caminho do callback
# --------------------------------------------------------------------------- #
def test_pagina_de_retorno_aceita_o_callback_do_google():
    html = (ROOT / "site" / "oauth" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"!/(\^\\/integrations.*?\$)/\.test\(cb\)", html)
    assert m, "regex do cb não encontrada na página de retorno"
    padrao = m.group(1).replace("\\/", "/")
    assert re.fullmatch(padrao, gs.CALLBACK_PATH)
    assert not re.fullmatch(padrao, "/auth/login")
    assert 'name="referrer" content="no-referrer"' in html
    assert gs.get_settings().oauth_relay_url.rstrip("/").endswith("/oauth")


# --------------------------------------------------------------------------- #
# Tool: principal, conta nomeada e fallback
# --------------------------------------------------------------------------- #
CONTAS = [{"id": "a1", "email": "principal@gmail.com"}, {"id": "a2", "email": "reserva@gmail.com"}]


def _sift(fallback: bool, tokens: dict, monkeypatch):
    from sift import Sift
    from aiworkspace.tools import sift_service

    async def fake_token(account_id):
        return tokens.get(account_id)

    monkeypatch.setattr(gs, "get_access_token", fake_token)
    monkeypatch.setattr(gs, "gmail_search", lambda tok, q, n: {"messages": [], "count": 0, "tok": tok})
    s = Sift()
    cfg = sift_service.google_config_from_secrets("u1", CONTAS, {}, fallback=fallback)
    sift_service._register_builtins(s, sift_service.SearchConfig(), {"google.gmail.mailbox"}, google_cfg=cfg)
    s.build_index()
    return s


def _busca(s, **extra):
    raw = s.dispatch("execute_tool", {"path": "google.gmail.mailbox", "params": {"action": "search", "query": "x", **extra}})
    return json.loads(raw) if isinstance(raw, str) else raw


def test_sem_conta_no_pedido_usa_a_principal(monkeypatch):
    out = _busca(_sift(False, {"a1": "t1", "a2": "t2"}, monkeypatch))
    assert out.get("account_email") == "principal@gmail.com" and "question" not in out


def test_fallback_desligado_nao_troca_de_caixa(monkeypatch):
    out = _busca(_sift(False, {"a2": "t2"}, monkeypatch))
    assert "principal@gmail.com" in out["error"]


def test_fallback_ligado_usa_a_proxima_e_avisa(monkeypatch):
    out = _busca(_sift(True, {"a2": "t2"}, monkeypatch))
    assert out["account_email"] == "reserva@gmail.com" and "principal@gmail.com" in out["note"]


def test_conta_nomeada_nunca_cai_em_outra(monkeypatch):
    out = _busca(_sift(True, {"a2": "t2"}, monkeypatch), account="principal@gmail.com")
    assert "error" in out
