"""Consultas públicas, somente leitura, para pesquisa técnica e de segurança.

As funções deste módulo são usadas pelas tools SIFT. Elas retornam metadados
compactos e links de origem; em especial, a consulta ao Exploit-DB nunca baixa
nem executa o arquivo do exploit. O índice CSV do Exploit-DB é relativamente
grande, então é mantido em memória por algumas horas para não transformar cada
pergunta do agente em um download de ~10 MB.
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from typing import Any

import httpx

_UA = "AI-Workspace/1.0 (public research tool)"
_GITHUB_API = "https://api.github.com/search"
_NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_EXPLOITDB_CSV = "https://gitlab.com/exploit-database/exploitdb/-/raw/main/files_exploits.csv"
_EXPLOITDB_TTL_SECONDS = 6 * 60 * 60
_RESULT_TTL_SECONDS = 5 * 60

_result_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_result_cache_lock = threading.Lock()
_exploit_index: tuple[float, list[dict[str, str]]] | None = None
_exploit_index_lock = threading.Lock()


def _clean_query(query: Any, *, min_length: int = 1) -> str:
    value = " ".join(str(query or "").split()).strip()
    if len(value) < min_length:
        raise ValueError("informe um termo de busca")
    return value[:256]


def _limit(value: Any, default: int = 10, maximum: int = 20) -> int:
    try:
        return max(1, min(int(value if value is not None else default), maximum))
    except (TypeError, ValueError):
        return default


def _cache_get(key: str) -> dict[str, Any] | None:
    with _result_cache_lock:
        item = _result_cache.get(key)
        if item and time.monotonic() - item[0] < _RESULT_TTL_SECONDS:
            # A tool runner não deve conseguir alterar o objeto que está em cache.
            return {**item[1], "cached": True}
    return None


def _cache_put(key: str, result: dict[str, Any]) -> dict[str, Any]:
    with _result_cache_lock:
        _result_cache[key] = (time.monotonic(), result)
    return result


def _http_error(service: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 403:
        return {"error": f"{service} limitou temporariamente as consultas; tente novamente em instantes."}
    if response.status_code == 429:
        return {"error": f"{service} está com muitas consultas; tente novamente em instantes."}
    return {"error": f"{service} respondeu HTTP {response.status_code}."}


def github_public_search(query: Any, kind: str = "repositories", limit: Any = 10) -> dict[str, Any]:
    """Pesquisa repositórios, issues públicas ou usuários sem conta GitHub.

    A API pública do GitHub não libera code search sem autenticação. A ferramenta
    GitHub conectada já cobre busca de código nos repositórios que a conta acessa.
    """
    try:
        q = _clean_query(query, min_length=2)
    except ValueError as exc:
        return {"error": str(exc)}
    selected = (kind or "repositories").strip().lower()
    if selected not in {"repositories", "issues", "users"}:
        return {"error": "kind deve ser repositories, issues ou users. Para código, use a ferramenta GitHub da conta conectada."}
    n = _limit(limit)
    key = f"github:{selected}:{q.lower()}:{n}"
    if cached := _cache_get(key):
        return cached
    try:
        response = httpx.get(
            f"{_GITHUB_API}/{selected}",
            params={"q": q, "per_page": n},
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": _UA,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        return {"error": f"não foi possível consultar o GitHub: {exc}"}
    if response.status_code != 200:
        return _http_error("GitHub", response)
    try:
        payload = response.json()
    except ValueError:
        return {"error": "GitHub devolveu uma resposta inválida."}
    items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []
    results: list[dict[str, Any]] = []
    for item in items[:n]:
        if not isinstance(item, dict):
            continue
        if selected == "repositories":
            owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
            results.append({
                "full_name": item.get("full_name") or "",
                "description": (item.get("description") or "")[:300],
                "owner": owner.get("login") or "",
                "language": item.get("language") or "",
                "stars": item.get("stargazers_count") or 0,
                "updated_at": item.get("updated_at") or "",
                "url": item.get("html_url") or "",
            })
        elif selected == "issues":
            results.append({
                "title": (item.get("title") or "")[:300],
                "number": item.get("number"),
                "state": item.get("state") or "",
                "repository": (item.get("repository_url") or "").removeprefix("https://api.github.com/repos/"),
                "author": ((item.get("user") or {}).get("login") if isinstance(item.get("user"), dict) else "") or "",
                "updated_at": item.get("updated_at") or "",
                "url": item.get("html_url") or "",
            })
        else:
            results.append({
                "login": item.get("login") or "",
                "name": item.get("name") or "",
                "bio": (item.get("bio") or "")[:300],
                "followers": item.get("followers") or 0,
                "url": item.get("html_url") or "",
            })
    return _cache_put(key, {
        "source": "GitHub public REST API",
        "kind": selected,
        "total_count": payload.get("total_count", 0) if isinstance(payload, dict) else 0,
        "incomplete_results": bool(payload.get("incomplete_results")) if isinstance(payload, dict) else False,
        "results": results,
    })


def _cvss(cve: dict[str, Any]) -> dict[str, Any]:
    metrics = cve.get("metrics") if isinstance(cve.get("metrics"), dict) else {}
    for version in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(version)
        if not isinstance(entries, list) or not entries:
            continue
        entry = next((e for e in entries if isinstance(e, dict) and e.get("type") == "Primary"), entries[0])
        if not isinstance(entry, dict):
            continue
        data = entry.get("cvssData") if isinstance(entry.get("cvssData"), dict) else {}
        return {
            "version": data.get("version") or version.removeprefix("cvssMetric"),
            "score": data.get("baseScore"),
            "severity": data.get("baseSeverity") or entry.get("baseSeverity") or "",
            "vector": data.get("vectorString") or "",
        }
    return {}


def _english_description(cve: dict[str, Any]) -> str:
    descriptions = cve.get("descriptions") if isinstance(cve.get("descriptions"), list) else []
    for item in descriptions:
        if isinstance(item, dict) and item.get("lang") == "en":
            return str(item.get("value") or "")[:1000]
    for item in descriptions:
        if isinstance(item, dict):
            return str(item.get("value") or "")[:1000]
    return ""


def nvd_cve_search(query: Any, limit: Any = 10) -> dict[str, Any]:
    """Consulta a API 2.0 oficial do NVD por CVE ou palavra-chave."""
    try:
        q = _clean_query(query, min_length=2)
    except ValueError as exc:
        return {"error": str(exc)}
    n = _limit(limit)
    key = f"nvd:{q.lower()}:{n}"
    if cached := _cache_get(key):
        return cached
    params: dict[str, Any] = {"resultsPerPage": n}
    if re.fullmatch(r"CVE-\d{4}-\d{4,}", q, flags=re.IGNORECASE):
        params["cveId"] = q.upper()
    else:
        params["keywordSearch"] = q
    try:
        response = httpx.get(_NVD_API, params=params, headers={"User-Agent": _UA}, timeout=20)
    except httpx.HTTPError as exc:
        return {"error": f"não foi possível consultar o NVD: {exc}"}
    if response.status_code != 200:
        return _http_error("NVD", response)
    try:
        payload = response.json()
    except ValueError:
        return {"error": "NVD devolveu uma resposta inválida."}
    vulnerabilities = payload.get("vulnerabilities") if isinstance(payload, dict) else []
    if not isinstance(vulnerabilities, list):
        vulnerabilities = []
    results: list[dict[str, Any]] = []
    for wrapper in vulnerabilities[:n]:
        cve = wrapper.get("cve") if isinstance(wrapper, dict) and isinstance(wrapper.get("cve"), dict) else {}
        cve_id = str(cve.get("id") or "")
        refs = cve.get("references") if isinstance(cve.get("references"), list) else []
        results.append({
            "id": cve_id,
            "description": _english_description(cve),
            "published": cve.get("published") or "",
            "last_modified": cve.get("lastModified") or "",
            "cvss": _cvss(cve),
            "references": [str(ref.get("url")) for ref in refs if isinstance(ref, dict) and ref.get("url")][:5],
            "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}" if cve_id else "",
        })
    return _cache_put(key, {
        "source": "NVD CVE API 2.0",
        "total_results": payload.get("totalResults", 0) if isinstance(payload, dict) else 0,
        "results": results,
    })


def _load_exploit_index() -> tuple[list[dict[str, str]] | None, str | None, bool]:
    """Carrega o índice oficial uma vez por TTL e devolve se veio do cache."""
    global _exploit_index
    with _exploit_index_lock:
        if _exploit_index and time.monotonic() - _exploit_index[0] < _EXPLOITDB_TTL_SECONDS:
            return _exploit_index[1], None, True
        try:
            response = httpx.get(
                _EXPLOITDB_CSV,
                headers={"User-Agent": _UA},
                timeout=30,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            return None, f"não foi possível baixar o índice do Exploit-DB: {exc}", False
        if response.status_code != 200:
            return None, _http_error("Exploit-DB", response)["error"], False
        try:
            rows = csv.DictReader(io.StringIO(response.text))
            index = [
                {
                    "id": (row.get("id") or "").strip(),
                    "description": (row.get("description") or "").strip(),
                    "date_published": (row.get("date_published") or "").strip(),
                    "author": (row.get("author") or "").strip(),
                    "type": (row.get("type") or "").strip(),
                    "platform": (row.get("platform") or "").strip(),
                    "port": (row.get("port") or "").strip(),
                    "codes": (row.get("codes") or "").strip(),
                    "tags": (row.get("tags") or "").strip(),
                }
                for row in rows
                if (row.get("id") or "").strip() and (row.get("description") or "").strip()
            ]
        except (csv.Error, UnicodeError) as exc:
            return None, f"índice do Exploit-DB inválido: {exc}", False
        _exploit_index = (time.monotonic(), index)
        return index, None, False


def exploitdb_search(query: Any, limit: Any = 10) -> dict[str, Any]:
    """Pesquisa metadados do catálogo público do Exploit-DB, sem buscar o PoC."""
    try:
        q = _clean_query(query, min_length=2)
    except ValueError as exc:
        return {"error": str(exc)}
    n = _limit(limit)
    key = f"exploitdb:{q.lower()}:{n}"
    if cached := _cache_get(key):
        return cached
    index, error, index_cached = _load_exploit_index()
    if error or index is None:
        return {"error": error or "índice do Exploit-DB indisponível."}
    terms = [part.lower() for part in re.findall(r"[\w.-]+", q) if len(part) >= 2]
    if not terms:
        return {"error": "informe termos de busca com ao menos 2 caracteres"}
    scored: list[tuple[int, dict[str, str]]] = []
    phrase = q.lower()
    for item in index:
        desc = item["description"].lower()
        codes = item["codes"].lower()
        other = " ".join((item["platform"], item["type"], item["tags"])).lower()
        haystack = f"{desc} {codes} {other}"
        if not all(term in haystack for term in terms):
            continue
        score = sum(8 if term in codes else 5 if term in desc else 2 for term in terms)
        if phrase in desc:
            score += 20
        scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    results = [
        {
            "edb_id": item["id"],
            "title": item["description"][:500],
            "published": item["date_published"],
            "author": item["author"][:160],
            "type": item["type"],
            "platform": item["platform"],
            "port": item["port"],
            "cve_references": item["codes"][:300],
            "url": f"https://www.exploit-db.com/exploits/{item['id']}",
        }
        for _, item in scored[:n]
    ]
    return _cache_put(key, {
        "source": "Exploit Database official index",
        "index_cached": index_cached,
        "note": "Retorna metadados e links; não baixa nem executa código de exploit.",
        "total_matches": len(scored),
        "results": results,
    })
