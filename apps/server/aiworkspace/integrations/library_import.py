"""Importa uma Skill (ou um texto para virar Prompt) a partir de uma URL.

Alvo principal: o formato "SKILL.md" (frontmatter YAML com `name`/`description` +
corpo em markdown) usado pelos repositórios públicos de skills. Resolve as três
formas de link do GitHub — arquivo (`/blob/`), pasta (`/tree/`) e raiz do repo —
além de `raw.githubusercontent.com` e de qualquer página comum.

DECISÃO (escopo): importamos **apenas o SKILL.md**. Uma Skill aqui é UM campo de
markdown, enquanto uma skill "de pasta" traz `scripts/` e `references/`; achatar
tudo incharia o conteúdo e cada uso custaria muito mais contexto. Os auxiliares são
apenas LISTADOS (nome + link) no fim do conteúdo, para o usuário/IA buscarem se
precisarem.

Nada aqui escreve no banco: o resultado vira uma PROPOSTA que o usuário aprova num
card. Nunca levanta p/ fora — devolve {"error": ...}.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; AIWorkspace/1.0)"
_TIMEOUT = 20.0
_MAX_BYTES = 400_000
MAX_CONTENT = 200_000
# nomes aceitos como "o arquivo da skill" dentro de uma pasta (ordem de preferência)
_SKILL_FILES = ("skill.md", "readme.md", "index.md")
_GH_API = "https://api.github.com"


# --------------------------------------------------------------------------- #
# HTTP (costura isolada p/ os testes)
# --------------------------------------------------------------------------- #
async def _get(url: str, *, as_json: bool = False) -> Any:
    headers = {"User-Agent": _UA}
    if as_json:
        headers["Accept"] = "application/vnd.github+json"
    async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
        r = await client.get(url, headers=headers)
    r.raise_for_status()
    if as_json:
        return r.json()
    return r.text[:_MAX_BYTES]


# --------------------------------------------------------------------------- #
# Frontmatter
# --------------------------------------------------------------------------- #
_FM_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """(metadados, corpo). Parser mínimo de escalares `chave: valor` do topo — é
    tudo que um SKILL.md usa, e evita uma dependência de YAML só para isto."""
    m = _FM_RE.match(text or "")
    if not m:
        return {}, (text or "").strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t", "-")):
            continue  # ignora aninhamento/listas: só chaves de 1º nível
        key, sep, val = line.partition(":")
        if not sep:
            continue
        k = key.strip().lower()
        v = val.strip().strip("\"'").strip()
        if k and v:
            meta[k] = v
    return meta, m.group(2).strip()


def _title_from_markdown(body: str) -> str:
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()[:255]
    return ""


def slugify(text: str, fallback: str = "skill", sep: str = "_") -> str:
    """Identificador válido. ATENÇÃO aos dois formatos DIFERENTES do backend:
    slug de Skill é `^[a-z0-9][a-z0-9_]*$` (underscore) e comando de Prompt é
    `^[a-z0-9][a-z0-9-]*$` (hífen) — usar o separador errado devolve 422 na hora
    de salvar. Por isso `sep` é explícito em vez de fixo."""
    s = re.sub(r"[^a-z0-9]+", sep, (text or "").lower().strip()).strip(sep)
    return s[:64] or fallback


# --------------------------------------------------------------------------- #
# Resolução de URL do GitHub
# --------------------------------------------------------------------------- #
def parse_github(url: str) -> dict[str, str] | None:
    """(owner, repo, ref, path, kind) de uma URL do GitHub — ou None se não for."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    parts = [p for p in (u.path or "").split("/") if p]
    if host in ("raw.githubusercontent.com",) and len(parts) >= 4:
        return {"owner": parts[0], "repo": parts[1], "ref": parts[2],
                "path": "/".join(parts[3:]), "kind": "file"}
    if host not in ("github.com", "www.github.com") or len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if len(parts) >= 4 and parts[2] in ("blob", "tree"):
        return {"owner": owner, "repo": repo, "ref": parts[3],
                "path": "/".join(parts[4:]), "kind": "file" if parts[2] == "blob" else "dir"}
    return {"owner": owner, "repo": repo, "ref": "", "path": "", "kind": "repo"}


def raw_url(gh: dict[str, str]) -> str:
    return (f"https://raw.githubusercontent.com/{gh['owner']}/{gh['repo']}/"
            f"{gh['ref']}/{gh['path']}")


async def _github_dir(gh: dict[str, str]) -> list[dict[str, Any]]:
    """Conteúdo de uma pasta do repo (API pública, sem token — sujeita a limite)."""
    ref = f"?ref={gh['ref']}" if gh.get("ref") else ""
    api = f"{_GH_API}/repos/{gh['owner']}/{gh['repo']}/contents/{gh['path']}{ref}"
    data = await _get(api, as_json=True)
    return data if isinstance(data, list) else []


async def _from_github_dir(gh: dict[str, str]) -> dict[str, Any]:
    """Acha o SKILL.md na pasta e LISTA os auxiliares (sem baixá-los)."""
    entries = await _github_dir(gh)
    files = {str(e.get("name", "")).lower(): e for e in entries if e.get("type") == "file"}
    chosen = next((files[n] for n in _SKILL_FILES if n in files), None)
    if chosen is None:
        raise ValueError("nenhum SKILL.md/README.md encontrado nessa pasta")
    text = await _get(chosen.get("download_url") or raw_url(gh))
    aux = [
        {"name": str(e.get("name")), "url": e.get("html_url") or e.get("download_url") or "",
         "type": str(e.get("type"))}
        for e in entries
        if str(e.get("name", "")).lower() != str(chosen.get("name", "")).lower()
    ]
    return {"text": text, "aux": aux, "source_url": chosen.get("html_url") or raw_url(gh)}


# --------------------------------------------------------------------------- #
# HTML -> texto (páginas comuns)
# --------------------------------------------------------------------------- #
def _looks_html(text: str) -> bool:
    head = (text or "")[:400].lower()
    return "<html" in head or "<!doctype html" in head


def _html_to_text(html: str) -> str:
    # deep_search mora na RAIZ do pacote (aiworkspace/deep_search.py), não em tools/
    from ..deep_search import _html_to_text as strip_html

    return strip_html(html)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def _aux_note(aux: list[dict[str, Any]]) -> str:
    """Rodapé listando os arquivos auxiliares NÃO importados (decisão de escopo)."""
    if not aux:
        return ""
    lines = [f"- `{a['name']}`" + (f" — {a['url']}" if a.get("url") else "") for a in aux[:20]]
    return (
        "\n\n---\n\n## Arquivos auxiliares (não importados)\n"
        "Esta skill veio de uma pasta que contém também os itens abaixo. "
        "Busque-os pela URL se precisar deles:\n" + "\n".join(lines)
    )


async def fetch_skill(url: str) -> dict[str, Any]:
    """Baixa e monta a proposta de skill a partir de `url`."""
    aux: list[dict[str, Any]] = []
    source_url = url
    gh = parse_github(url)
    try:
        if gh and gh["kind"] in ("dir", "repo"):
            got = await _from_github_dir(gh)
            text, aux, source_url = got["text"], got["aux"], got["source_url"]
        elif gh and gh["kind"] == "file":
            text = await _get(raw_url(gh))
        else:
            text = await _get(url)
    except httpx.HTTPStatusError as exc:
        return {"error": f"não consegui baixar ({exc.response.status_code}) — o link é público?"}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao baixar: {str(exc)[:160]}"}

    if _looks_html(text):
        text = _html_to_text(text)
    if not (text or "").strip():
        return {"error": "o link não devolveu conteúdo de texto"}

    meta, body = parse_frontmatter(text)
    name = (meta.get("name") or _title_from_markdown(body) or "").strip()
    description = (meta.get("description") or "").strip()
    if not name:
        # último recurso: o nome do arquivo/pasta do link
        name = (urlparse(source_url).path.rstrip("/").split("/") or ["skill"])[-1]
        name = re.sub(r"\.(md|markdown|txt)$", "", name, flags=re.I) or "skill"
    content = (body + _aux_note(aux))[:MAX_CONTENT]
    if not description:
        description = f"Skill importada de {source_url}"
    return {
        "ok": True,
        "name": name[:255],
        "slug": slugify(meta.get("name") or name),
        "description": description[:2000],
        "content": content,
        "source_url": source_url,
        "aux_count": len(aux),
    }


async def fetch_text(url: str) -> dict[str, Any]:
    """Texto legível de uma URL — base para virar um Prompt."""
    gh = parse_github(url)
    try:
        text = await _get(raw_url(gh) if gh and gh["kind"] == "file" else url)
    except httpx.HTTPStatusError as exc:
        return {"error": f"não consegui baixar ({exc.response.status_code}) — o link é público?"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"falha ao baixar: {str(exc)[:160]}"}
    if _looks_html(text):
        text = _html_to_text(text)
    text = (text or "").strip()
    if not text:
        return {"error": "o link não devolveu conteúdo de texto"}
    meta, body = parse_frontmatter(text)
    title = (meta.get("title") or meta.get("name") or _title_from_markdown(body) or "").strip()
    return {"ok": True, "title": title[:255], "content": body[:MAX_CONTENT], "source_url": url}
