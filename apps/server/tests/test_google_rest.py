"""Gmail e Agenda por REST direto (sem google-api-python-client).

O cliente oficial pesava ~110 MB no motor (as descrições de todas as APIs do Google)
para as ~10 chamadas que fazemos. Os testes travam o que ele fazia por nós: o formato
exato da requisição — método, caminho, parâmetros repetidos, booleanos, escape do id
da agenda — e o erro legível quando o Google recusa.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from aiworkspace.integrations import google_service as gs


class _FakeGoogle:
    def __init__(self, respostas):
        self.respostas = respostas
        self.pedidos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        chave = (request.method, request.url.path)
        corpo = self.respostas.get(chave)
        if callable(corpo):
            corpo = corpo(request)
        if corpo is None:
            return httpx.Response(404, json={"error": {"message": f"sem rota {chave}"}})
        status, dados = corpo if isinstance(corpo, tuple) else (200, corpo)
        return httpx.Response(status, json=dados) if dados is not None else httpx.Response(status)


@pytest.fixture()
def google(monkeypatch):
    fake = _FakeGoogle({})
    real = httpx.Client

    def cliente(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(gs.httpx, "Client", cliente)
    return fake


def _q(req: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(req.url)).query)


def test_busca_paginada_conta_e_pede_metadados_certos(google):
    paginas = iter([
        {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        {"messages": [{"id": "c"}]},
    ])
    google.respostas[("GET", "/gmail/v1/users/me/messages")] = lambda r: next(paginas)
    for mid in "abc":
        google.respostas[("GET", f"/gmail/v1/users/me/messages/{mid}")] = {
            "id": mid, "snippet": f"oi {mid}",
            "payload": {"headers": [{"name": "Subject", "value": f"assunto {mid}"},
                                    {"name": "From", "value": "x@y.com"}]},
        }

    out = gs.gmail_search("TOKEN", "is:unread", 0)  # 0 = contar tudo

    assert out["count"] == 3
    assert [m["subject"] for m in out["messages"]] == ["assunto a", "assunto b", "assunto c"]
    lista = [r for r in google.pedidos if r.url.path.endswith("/messages")]
    assert _q(lista[0])["q"] == ["is:unread"] and "pageToken" not in _q(lista[0])
    assert _q(lista[1])["pageToken"] == ["p2"]
    meta = next(r for r in google.pedidos if r.url.path.endswith("/messages/a"))
    # lista vira parâmetro REPETIDO, como a API espera
    assert _q(meta) == {"format": ["metadata"], "metadataHeaders": ["From", "Subject", "Date"]}
    assert all(r.headers["authorization"] == "Bearer TOKEN" for r in google.pedidos)


def test_busca_sem_consulta_nao_manda_q_vazio(google):
    google.respostas[("GET", "/gmail/v1/users/me/messages")] = {"messages": []}

    gs.gmail_search("T", "", 5)

    assert "q" not in _q(google.pedidos[0])


def test_enviar_email_manda_o_raw(google):
    google.respostas[("POST", "/gmail/v1/users/me/messages/send")] = {"id": "enviado-1"}

    out = gs.gmail_send("T", "a@b.com", "Oi", "corpo")

    assert out == {"ok": True, "id": "enviado-1"}
    assert set(json.loads(google.pedidos[0].content)) == {"raw"}


@pytest.mark.parametrize("acao,caminho,corpo", [
    ("trash", "/gmail/v1/users/me/messages/m1/trash", None),
    ("archive", "/gmail/v1/users/me/messages/m1/modify", {"removeLabelIds": ["INBOX"]}),
    ("star", "/gmail/v1/users/me/messages/m1/modify", {"addLabelIds": ["STARRED"]}),
])
def test_modificar_email(google, acao, caminho, corpo):
    google.respostas[("POST", caminho)] = {}

    out = gs.gmail_modify("T", "m1", acao)

    assert out["ok"] and google.pedidos[0].url.path == caminho
    if corpo is not None:
        assert json.loads(google.pedidos[0].content) == corpo


def test_agenda_com_id_de_email_vai_escapado_e_booleano_minusculo(google):
    """O id de uma agenda compartilhada é um e-mail — sem escape, o '@' e o '#' de
    ids como `pt.brazilian#holiday@group.v.calendar.google.com` quebrariam a URL."""
    cid = "pt.brazilian#holiday@group.v.calendar.google.com"
    google.respostas[("GET", "/calendar/v3/calendars/" + cid + "/events")] = {"items": []}

    gs.cal_list("T", "", "", 10, calendar_id=cid)

    req = google.pedidos[0]
    assert "%23holiday%40group" in str(req.url)  # '#' e '@' escapados no caminho
    assert _q(req)["singleEvents"] == ["true"] and _q(req)["orderBy"] == ["startTime"]


def test_criar_alterar_apagar_evento(google):
    ev = {"id": "e1", "summary": "Reunião", "start": {"dateTime": "2026-09-22T10:00:00-03:00"},
          "end": {"dateTime": "2026-09-22T11:00:00-03:00"}}
    google.respostas[("POST", "/calendar/v3/calendars/primary/events")] = ev
    google.respostas[("PATCH", "/calendar/v3/calendars/primary/events/e1")] = {**ev, "summary": "Nova"}
    google.respostas[("DELETE", "/calendar/v3/calendars/primary/events/e1")] = (204, None)

    assert gs.cal_create("T", "Reunião", "2026-09-22T10:00", "2026-09-22T11:00", tz="America/Sao_Paulo")["ok"]
    assert gs.cal_update("T", "e1", summary="Nova")["ok"]
    assert gs.cal_delete("T", "e1") == {"ok": True, "id": "e1", "deleted": True}
    assert json.loads(google.pedidos[1].content) == {"summary": "Nova"}  # PATCH só com o que mudou


def test_recusa_do_google_vira_erro_legivel(google):
    google.respostas[("GET", "/gmail/v1/users/me/messages/x")] = (
        403, {"error": {"message": "Request had insufficient authentication scopes."}})

    with pytest.raises(RuntimeError, match="Google 403: Request had insufficient authentication scopes"):
        gs.gmail_get("T", "x")
