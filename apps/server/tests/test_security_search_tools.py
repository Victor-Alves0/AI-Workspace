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
    search._NVD_MIN_INTERVAL = 0.0  # o respiro do limite público do NVD não vale p/ teste


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
    monkeypatch.setattr(search, "nvd_cve_search", lambda query, limit, **kw: {"results": [{"id": query}], "limit": limit})
    raw = sift.dispatch("execute_tool", {"path": "security.cve.search", "params": {"query": "CVE-2024-1", "limit": 3}})
    out = json.loads(raw) if isinstance(raw, str) else raw
    # A SIFT filtra a resposta pelos campos declarados em `returns`; o importante
    # aqui é provar que o path foi registrado e chegou ao handler certo.
    assert out == {"results": [{"id": "CVE-2024-1"}]}


# ------------------------------- NVD (01/10) --------------------------------------
def _cve(cid, published, **extra):
    return {"cve": {"id": cid, "published": published, "descriptions": [{"lang": "en", "value": cid}],
                    **extra}}


def test_nvd_varias_palavras_sem_resultado_tenta_so_o_produto(monkeypatch):
    """O NVD exige TODAS as palavras na descrição: "InfluxDB information disclosure
    vulnerability 2.7.5 2024" dava 0 — "InfluxDB" sozinho acha."""
    pedidos = []

    def fake_get(url, *, params, headers, timeout):
        pedidos.append(params.get("keywordSearch"))
        if params["keywordSearch"] == "InfluxDB":
            return _response(200, {"totalResults": 1, "vulnerabilities": [_cve("CVE-2022-36640", "2022-09-02")]})
        return _response(200, {"totalResults": 0, "vulnerabilities": []})

    monkeypatch.setattr(search.httpx, "get", fake_get)
    out = search.nvd_cve_search("InfluxDB information disclosure vulnerability 2.7.5 2024", 5)
    assert pedidos == ["InfluxDB information disclosure vulnerability 2.7.5 2024", "InfluxDB 2.7.5", "InfluxDB"]
    assert out["query_used"] == "InfluxDB" and out["results"][0]["id"] == "CVE-2022-36640"
    assert "note" in out


def test_nvd_mostra_os_mais_recentes_primeiro(monkeypatch):
    """A API devolve do mais antigo ao mais novo: "Hikvision" trazia só CVEs de 2014."""
    pedidos = []

    def fake_get(url, *, params, headers, timeout):
        pedidos.append(params.get("startIndex"))
        if params.get("startIndex") is None:
            return _response(200, {"totalResults": 48, "vulnerabilities": [_cve("CVE-2013-4977", "2014-01-01")]})
        return _response(200, {"totalResults": 48, "vulnerabilities": [
            _cve("CVE-2026-1", "2026-07-22"), _cve("CVE-2026-2", "2026-09-10")]})

    monkeypatch.setattr(search.httpx, "get", fake_get)
    out = search.nvd_cve_search("Hikvision", 2)
    assert pedidos == [None, 46]
    assert [r["id"] for r in out["results"]] == ["CVE-2026-2", "CVE-2026-1"]


def test_nvd_traz_versoes_afetadas_cwe_e_kev_e_repassa_filtros(monkeypatch):
    vistos = {}

    def fake_get(url, *, params, headers, timeout):
        vistos.update(params)
        return _response(200, {"totalResults": 1, "vulnerabilities": [_cve(
            "CVE-2021-36260", "2021-09-22",
            weaknesses=[{"description": [{"lang": "en", "value": "CWE-78"}]}],
            configurations=[{"nodes": [{"cpeMatch": [
                {"vulnerable": True, "criteria": r"cpe:2.3:o:hikvision:ds-2cd2026g2-iu\/sl_firmware:*:*:*:*:*:*:*:*",
                 "versionEndExcluding": "5.5.800"},
                {"vulnerable": False, "criteria": "cpe:2.3:h:hikvision:camera:-:*:*:*:*:*:*:*"},
            ]}]}],
            cisaExploitAdd="2022-01-10", cisaActionDue="2022-01-24",
            cisaRequiredAction="Apply updates per vendor instructions.",
        )]})

    monkeypatch.setattr(search.httpx, "get", fake_get)
    out = search.nvd_cve_search("Hikvision", 5, severity="critical", known_exploited=True)
    r = out["results"][0]
    assert vistos["cvssV3Severity"] == "CRITICAL" and "hasKev" in vistos
    assert r["cwe"] == ["CWE-78"]
    assert r["affected"] == ["hikvision ds-2cd2026g2-iu/sl_firmware < 5.5.800"]
    assert r["known_exploited"]["added"] == "2022-01-10"


def test_nvd_sem_kev_nao_inventa_o_campo(monkeypatch):
    monkeypatch.setattr(search.httpx, "get", lambda url, **k: _response(
        200, {"totalResults": 1, "vulnerabilities": [_cve("CVE-2024-1", "2024-01-01")]}))
    r = search.nvd_cve_search("CVE-2024-1", 1)["results"][0]
    assert "known_exploited" not in r
