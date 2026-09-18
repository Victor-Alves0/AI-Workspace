"""Clientes de integração que não tinham teste: Notion, Slack, ElevenLabs, Spotify, Vercel.

Duas classes de falha justificam estes testes:

1. **Autenticação silenciosa** — cada provedor tem seu jeito (Bearer, Basic, header
   próprio, versão de API obrigatória). Errar isso não quebra o build: a integração
   simplesmente "não funciona", e a descoberta acontece com o usuário na frente.
2. **Vazamento do segredo** — a mensagem de erro de uma tool volta para o MODELO e fica
   no histórico do chat. Um erro que carregue a chave a publica ali.

Herméticos: `httpx` é trocado por um fake; nada de rede.
"""
from __future__ import annotations

import base64
import pytest

from aiworkspace.integrations import elevenlabs_service as el
from aiworkspace.integrations import notion_service as notion
from aiworkspace.integrations import slack_service as slack
from aiworkspace.integrations import spotify_service as spotify
from aiworkspace.integrations import vercel_service as vercel

TOKEN = "segredo-do-usuario-123"


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text or str(self._data)
        self.content = b""
        self.headers = {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAsync:
    """Substitui `httpx.AsyncClient`, guardando as chamadas feitas."""

    calls: list = []
    reply = staticmethod(lambda method, url, kwargs: _Resp())

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _record(self, method, url, **kwargs):
        _FakeAsync.calls.append({"method": method, "url": url, **kwargs})
        return _FakeAsync.reply(method, url, kwargs)

    async def get(self, url, **kwargs):
        return await self._record("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._record("POST", url, **kwargs)


def _patch_async(monkeypatch, module, reply=None):
    _FakeAsync.calls = []
    _FakeAsync.reply = staticmethod(reply or (lambda method, url, kwargs: _Resp()))
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsync)
    return _FakeAsync.calls


# --------------------------------------------------------------------------- #
# Vercel — Bearer                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_vercel_autentica_com_bearer_e_compacta_os_projetos(monkeypatch):
    calls = _patch_async(monkeypatch, vercel, lambda *_: _Resp(200, {"projects": [{
        "id": "prj_1", "name": "site", "framework": "nextjs",
        "latestDeployments": [{"a": "payload gigante"}] * 50,
    }]}))
    out = await vercel.list_projects(TOKEN)

    assert calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert calls[0]["url"].endswith("/v9/projects")
    # o resultado vai para o contexto do modelo: só o que serve p/ decidir
    assert out == [{"id": "prj_1", "name": "site", "framework": "nextjs"}]


@pytest.mark.asyncio
async def test_vercel_token_invalido_nao_devolve_o_token_no_erro(monkeypatch):
    """A mensagem de erro volta ao modelo e fica no chat: não pode levar a chave."""
    _patch_async(monkeypatch, vercel, lambda *_: _Resp(401, {}, text="Not authorized"))
    out = await vercel.test_connection(TOKEN)

    assert out["ok"] is False
    assert TOKEN not in str(out)


# --------------------------------------------------------------------------- #
# Spotify — Client Credentials (Basic)                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_spotify_troca_credenciais_por_token_e_busca_com_bearer(monkeypatch):
    def reply(method, url, kwargs):
        if "accounts.spotify.com" in url:
            return _Resp(200, {"access_token": "tok-app"})
        return _Resp(200, {"tracks": {"items": [{
            "name": "Song", "artists": [{"name": "Band"}],
            "external_urls": {"spotify": "https://open.spotify.com/track/1"},
        }]}})

    calls = _patch_async(monkeypatch, spotify, reply)
    out = await spotify.search("client-id", "client-secret", "song")

    token_call, search_call = calls[0], calls[1]
    basic = base64.b64encode(b"client-id:client-secret").decode()
    assert token_call["headers"]["Authorization"] == f"Basic {basic}"
    assert token_call["data"] == {"grant_type": "client_credentials"}
    assert search_call["headers"]["Authorization"] == "Bearer tok-app"
    # o segredo do app não pode aparecer no que volta para o modelo
    assert "client-secret" not in str(out)
    assert out and out[0]["name"] == "Song"


# --------------------------------------------------------------------------- #
# Notion — Bearer + versão da API                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_notion_envia_a_versao_da_api(monkeypatch):
    """Sem o header `Notion-Version` a API recusa tudo — e o sintoma é "parou de
    funcionar do nada" quando a Notion muda o default."""
    calls = _patch_async(monkeypatch, notion, lambda *_: _Resp(200, {"name": "Victor"}))
    out = await notion.validate_token(TOKEN)

    headers = calls[0]["headers"]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Notion-Version"] == notion.API_VERSION
    assert calls[0]["url"] == f"{notion.API_BASE}/users/me"
    assert "error" not in out


@pytest.mark.asyncio
async def test_notion_token_ruim_vira_erro_sem_o_token(monkeypatch):
    _patch_async(monkeypatch, notion, lambda *_: _Resp(401, {"message": "API token is invalid"}))
    out = await notion.validate_token(TOKEN)

    assert out.get("error")
    assert TOKEN not in str(out)


# --------------------------------------------------------------------------- #
# Slack — HTTP 200 com ok:false                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_slack_recusa_token_mesmo_com_http_200(monkeypatch):
    """A Slack responde 200 e põe a falha no corpo (`ok:false`). Quem confia no código
    HTTP dá o token por válido e só descobre quando nada é entregue."""
    _patch_async(monkeypatch, slack, lambda *_: _Resp(200, {"ok": False, "error": "invalid_auth"}))
    out = await slack.validate_token(TOKEN)

    assert out == {"error": "invalid_auth"}
    assert "team" not in out


@pytest.mark.asyncio
async def test_slack_token_valido_traz_a_identidade_do_bot(monkeypatch):
    calls = _patch_async(monkeypatch, slack, lambda *_: _Resp(200, {
        "ok": True, "team": "Casa", "team_id": "T1", "user_id": "U1",
    }))
    out = await slack.validate_token(f"  {TOKEN}  ")   # espaços colados no copiar/colar

    assert calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert out == {"team": "Casa", "team_id": "T1", "bot_user_id": "U1"}


# --------------------------------------------------------------------------- #
# ElevenLabs — header próprio + cache de vozes                                 #
# --------------------------------------------------------------------------- #
def _patch_sync(monkeypatch, module, reply):
    calls: list = []

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "headers": headers or {}})
        return reply(url)

    monkeypatch.setattr(module.httpx, "get", fake_get)
    return calls


def test_elevenlabs_usa_o_header_proprio_e_nao_authorization(monkeypatch):
    """A ElevenLabs autentica por `xi-api-key`; mandar Bearer devolve 401."""
    el._voices_cache.clear()
    calls = _patch_sync(monkeypatch, el, lambda _url: _Resp(200, {"voices": [
        {"voice_id": "v1", "name": "Rachel"}, {"name": "sem id — ignorada"},
    ]}))
    vozes = el.list_voices("chave-el")

    assert calls[0]["headers"] == {"xi-api-key": "chave-el"}
    assert "Authorization" not in calls[0]["headers"]
    assert vozes == [{"id": "v1", "name": "Rachel"}]


def test_elevenlabs_falha_transitoria_serve_o_cache_em_vez_de_sumir_com_as_vozes(monkeypatch):
    """Sem isto, um 500 momentâneo esvaziava a lista e a voz escolhida no modelo
    aparecia como inexistente."""
    el._voices_cache.clear()
    _patch_sync(monkeypatch, el, lambda _url: _Resp(200, {"voices": [{"voice_id": "v1", "name": "Rachel"}]}))
    assert el.list_voices("chave-el") == [{"id": "v1", "name": "Rachel"}]

    def boom(_url):
        raise RuntimeError("502 Bad Gateway")

    _patch_sync(monkeypatch, el, boom)
    assert el.list_voices("chave-el", use_cache=False) == [{"id": "v1", "name": "Rachel"}]
    el._voices_cache.clear()


def test_elevenlabs_chave_invalida_nao_devolve_a_chave(monkeypatch):
    _patch_sync(monkeypatch, el, lambda _url: _Resp(401, {}, text="unauthorized"))
    out = el.test_connection("chave-el")

    assert out["ok"] is False
    assert "chave-el" not in str(out)


def test_voz_por_nome_resolve_para_o_id(monkeypatch):
    """No editor de modelos a voz é escolhida pelo NOME; a API só aceita o id."""
    el._voices_cache.clear()
    _patch_sync(monkeypatch, el, lambda _url: _Resp(200, {"voices": [
        {"voice_id": "v-rachel", "name": "Rachel"},
    ]}))
    assert el.resolve_voice("chave-el", "Rachel") == "v-rachel"
    assert el.resolve_voice("chave-el", "v-rachel") == "v-rachel"
    el._voices_cache.clear()
