"""Ferramentas públicas de pesquisa GitHub / Exploit-DB / CVE.

Nada aqui faz rede: as respostas HTTP são dubladas para validar o formato compacto,
os limites e o registro real na SIFT.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from aiworkspace.tools import security_search as search
from aiworkspace.tools import sift_service


def _response(status: int, payload: dict | None = None, text: str = ""):
    return SimpleNamespace(status_code=status, json=lambda: payload or {}, text=text)


def setup_function():
    search._result_cache.clear()
    search._exploit_index = None


def test_public_github_search_shapes_repository_results_and_caches(monkeypatch):
    seen: list[dict] = []

    def fake_get(url, *, params, headers, timeout):
        seen.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _response(200, {"total_count": 1, "incomplete_results": False, "items": [{
            "full_name": "tiangolo/fastapi", "description": "API framework",
            "owner": {"login": "tiangolo"}, "language": "Python", "stargazers_count": 123,
            "updated_at": "2026-01-01", "html_url": "https://github.com/tiangolo/fastapi",
        }]})

    monkeypatch.setattr(search.httpx, "get", fake_get)
    out = search.github_public_search("fastapi", "repositories", 5)
    again = search.github_public_search("fastapi", "repositories", 5)
    assert out["results"][0]["full_name"] == "tiangolo/fastapi"
    assert out["results"][0]["stars"] == 123
    assert again["cached"] is True and len(seen) == 1
    assert seen[0]["url"].endswith("/repositories")
    assert seen[0]["params"] == {"q": "fastapi", "per_page": 5}


def test_public_github_rejects_code_search_without_a_connected_account():
    out = search.github_public_search("router", "code")
    assert "conta conectada" in out["error"]


def test_nvd_search_uses_cve_id_and_keeps_cvss_and_references(monkeypatch):
    seen = {}

    def fake_get(url, *, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return _response(200, {"totalResults": 1, "vulnerabilities": [{"cve": {
            "id": "CVE-2024-3094", "published": "2024-03-29T00:00:00.000",
            "lastModified": "2024-04-01T00:00:00.000",
            "descriptions": [{"lang": "en", "value": "XZ Utils backdoor."}],
            "metrics": {"cvssMetricV31": [{"type": "Primary", "cvssData": {
                "version": "3.1", "baseScore": 10.0, "baseSeverity": "CRITICAL",
                "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            }}]},
            "references": [{"url": "https://www.openwall.com/"}],
        }}]})

    monkeypatch.setattr(search.httpx, "get", fake_get)
    out = search.nvd_cve_search("cve-2024-3094", 2)
    result = out["results"][0]
    assert seen["url"] == search._NVD_API
    assert seen["params"] == {"resultsPerPage": 2, "cveId": "CVE-2024-3094"}
    assert result["cvss"] == {
        "version": "3.1", "score": 10.0, "severity": "CRITICAL",
        "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
    }
    assert result["references"] == ["https://www.openwall.com/"]
    assert result["url"].endswith("CVE-2024-3094")


def test_exploitdb_search_filters_csv_but_never_returns_or_fetches_exploit_code(monkeypatch):
    calls = []
    csv_text = (
        "id,file,description,date_published,author,type,platform,port,codes,tags\n"
        "12345,exploits/linux/remote/12345.py,Widget 1.0 - Remote Code Execution,2024-01-01,Alice,remote,linux,443,CVE-2024-9999,widget\n"
        "12346,exploits/linux/dos/12346.txt,Other service - DoS,2024-01-02,Bob,dos,linux,,,other\n"
    )

    def fake_get(url, *, headers, timeout, follow_redirects):
        calls.append(url)
        return _response(200, text=csv_text)

    monkeypatch.setattr(search.httpx, "get", fake_get)
    out = search.exploitdb_search("CVE-2024-9999", 5)
    assert calls == [search._EXPLOITDB_CSV]
    assert out["total_matches"] == 1
    row = out["results"][0]
    assert row["edb_id"] == "12345" and row["url"].endswith("/12345")
    assert "file" not in row and "code" not in row
    assert "não baixa" in out["note"]


def test_security_tools_are_listed_and_registered_in_sift(monkeypatch):
    paths = {tool["path"] for tool in sift_service.system_tools()}
    wanted = {"github.public.search", "security.exploitdb.search", "security.cve.search"}
    assert wanted <= paths

    from sift import Sift
    sift = Sift()
    sift_service._register_builtins(sift, sift_service.SearchConfig(), wanted)
    sift.build_index()
    monkeypatch.setattr(search, "nvd_cve_search", lambda query, limit: {"results": [{"id": query}], "limit": limit})
    raw = sift.dispatch("execute_tool", {"path": "security.cve.search", "params": {"query": "CVE-2024-1", "limit": 3}})
    out = json.loads(raw) if isinstance(raw, str) else raw
    # A SIFT filtra a resposta pelos campos declarados em `returns`; o importante
    # aqui é provar que o path foi registrado e chegou ao handler certo.
    assert out == {"results": [{"id": "CVE-2024-1"}]}
