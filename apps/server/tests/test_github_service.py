"""Testes do github_service: montagem de request/headers, shaping e erros.

httpx.Client é monkeypatchado por um fake — nenhuma chamada de rede real."""
from __future__ import annotations

import base64

import pytest

from aiworkspace.integrations import github_service as gh


class FakeResp:
    def __init__(self, status=200, json_data=None, headers=None):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = str(self._json)

    def json(self):
        return self._json


class FakeClient:
    """Substitui httpx.Client: grava a última request e devolve a resposta roteada."""
    last = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, headers=None, params=None, json=None):
        FakeClient.last = {"method": method, "url": url, "headers": headers, "params": params, "json": json}
        return FakeClient.route(method, url, params, json)


def _patch(monkeypatch, route):
    FakeClient.route = staticmethod(route)
    monkeypatch.setattr(gh.httpx, "Client", FakeClient)


# --------------------------------- headers -----------------------------------

def test_request_sets_auth_and_version(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(200, {"ok": 1}))
    gh._request("tok123", "GET", "/user")
    h = FakeClient.last["headers"]
    assert h["Authorization"] == "Bearer tok123"
    assert h["X-GitHub-Api-Version"] == gh.API_VERSION
    assert FakeClient.last["url"] == "https://api.github.com/user"


def test_request_error_raises_friendly(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(404, {"message": "Not Found"}))
    with pytest.raises(gh.GithubError) as e:
        gh._request("t", "GET", "/repos/x/y")
    assert "Not Found" in str(e.value)


def test_request_error_includes_validation_errors(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(422, {"message": "Validation Failed", "errors": [{"field": "title"}]}))
    with pytest.raises(gh.GithubError) as e:
        gh._request("t", "POST", "/repos/x/y/issues")
    assert "Validation Failed" in str(e.value) and "title" in str(e.value)


# ------------------------------ get_file (base64) ----------------------------

def test_get_file_decodes_base64(monkeypatch):
    payload = base64.b64encode("linha1\nlinha2".encode()).decode()
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(200, {
        "path": "README.md", "sha": "abc", "size": 12, "encoding": "base64",
        "content": payload, "html_url": "http://x",
    }))
    out = gh.get_file("t", "me/repo", "README.md")
    assert out["type"] == "file" and out["content"] == "linha1\nlinha2" and out["sha"] == "abc"


def test_get_file_directory_listing(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(200, [
        {"name": "a.py", "type": "file", "path": "src/a.py"},
        {"name": "sub", "type": "dir", "path": "src/sub"},
    ]))
    out = gh.get_file("t", "me/repo", "src")
    assert out["type"] == "dir" and len(out["entries"]) == 2


# ------------------------------ issues / PRs ---------------------------------

def test_list_issues_filters_shape(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(200, [
        {"number": 1, "title": "Bug", "state": "open", "user": {"login": "vic"},
         "html_url": "http://i/1", "comments": 2},
    ]))
    rows = gh.list_issues("t", "me/repo", "open")
    assert rows[0] == {"number": 1, "title": "Bug", "state": "open", "user": "vic",
                       "url": "http://i/1", "is_pr": False, "body": "", "comments": 2}
    assert FakeClient.last["params"]["state"] == "open"


def test_create_issue_posts_body(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(201, {"number": 7, "html_url": "http://i/7"}))
    out = gh.create_issue("t", "me/repo", "Título", "corpo")
    assert out == {"ok": True, "number": 7, "url": "http://i/7"}
    assert FakeClient.last["method"] == "POST"
    assert FakeClient.last["json"] == {"title": "Título", "body": "corpo"}


def test_put_file_encodes_content(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(200, {"commit": {"sha": "c1"}, "content": {"html_url": "http://f"}}))
    gh.put_file("t", "me/repo", "docs/x.md", "olá", "msg", branch="main")
    sent = FakeClient.last["json"]
    assert base64.b64decode(sent["content"]).decode() == "olá"
    assert sent["message"] == "msg" and sent["branch"] == "main"
    assert FakeClient.last["method"] == "PUT"


def test_search_code_scopes_repo(monkeypatch):
    _patch(monkeypatch, lambda m, u, p, j: FakeResp(200, {"items": [
        {"repository": {"full_name": "me/repo"}, "path": "a.py", "html_url": "http://x"},
    ]}))
    rows = gh.search_code("t", "TODO", repo="me/repo")
    assert rows[0]["repo"] == "me/repo"
    assert FakeClient.last["params"]["q"] == "TODO repo:me/repo"
