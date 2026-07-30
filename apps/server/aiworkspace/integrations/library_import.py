"""Importa uma Skill (ou um texto para virar Prompt) a partir de uma URL.

Alvo principal: o formato "SKILL.md" (frontmatter YAML com `name`/`description` +
corpo em markdown) usado pelos repositórios públicos de skills. Resolve as três
formas de link do GitHub — arquivo (`/blob/`), pasta (`/tree/`) e raiz do repo —
além de `raw.githubusercontent.com` e de qualquer página comum.

ESCOPO: importamos o **SKILL.md** (o índice) + os **arquivos de referência de texto**
da pasta (`references/`, `scripts/`, arquivos soltos) como `files` — carregados sob
demanda via `view_skill(slug, file=...)`, então não incham o contexto do turno. Só
arquivos de TEXTO dentro de limites entram; binários/grandes ficam apenas LISTADOS
(nome + link) no rodapé do conteúdo para o usuário buscar se precisar.

Nada aqui escreve no banco: o resultado vira uma PROPOSTA que o usuário aprova num
card. Nunca levanta p/ fora — devolve {"error": ...}.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
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

# arquivos de referência: quais baixar (texto) e limites (espelham o backend em
# skills_routes: _MAX_FILES/_MAX_FILE_BYTES/_MAX_FILES_BYTES).
_REF_TEXT_EXT = (
    ".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv",
    ".tsv", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".tsx", ".jsx", ".sh",
    ".bash", ".sql", ".css", ".ini", ".cfg", ".env", ".go", ".rb", ".java", ".c",
    ".h", ".cpp", ".rs", ".php", ".pl", ".lua", ".r", ".gitignore", ".dockerfile",
)
_REF_MAX_FILES = 30
_REF_MAX_FILE_BYTES = 100_000
_REF_MAX_TOTAL_BYTES = 600_000
# subpastas percorridas p/ juntar referências (1 nível), além dos arquivos soltos
_REF_DIRS = ("references", "reference", "scripts", "assets", "examples", "templates", "docs")


def _is_ref_text(name: str) -> bool:
    n = (name or "").lower()
    return n.endswith(_REF_TEXT_EXT)


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


async def _download_ref(entry: dict[str, Any], name: str) -> dict[str, str] | None:
    """Baixa um arquivo de referência de texto (ou None se falhar/vazio)."""
    dl = entry.get("download_url")
    if not dl:
        return None
    try:
        text = await _get(dl)
    except Exception:  # noqa: BLE001 — um auxiliar que falha não derruba o import
        return None
    text = (text or "")[:_REF_MAX_FILE_BYTES]
    if not text.strip():
        return None
    return {"name": name, "content": text}


async def _from_github_dir(gh: dict[str, str]) -> dict[str, Any]:
    """Acha o SKILL.md na pasta, BAIXA os arquivos de referência de texto (soltos +
    1 nível de subpastas conhecidas) e LISTA o que sobrou (binários/grandes)."""
    entries = await _github_dir(gh)
    files = {str(e.get("name", "")).lower(): e for e in entries if e.get("type") == "file"}
    chosen = next((files[n] for n in _SKILL_FILES if n in files), None)
    if chosen is None:
        raise ValueError("nenhum SKILL.md/README.md encontrado nessa pasta")
    text = await _get(chosen.get("download_url") or raw_url(gh))
    chosen_name = str(chosen.get("name", "")).lower()

    ref_files: list[dict[str, str]] = []
    aux: list[dict[str, Any]] = []
    total = 0

    def _room() -> bool:
        return len(ref_files) < _REF_MAX_FILES and total < _REF_MAX_TOTAL_BYTES

    # 1) arquivos de texto soltos na raiz da pasta (menos o próprio SKILL.md)
    for e in entries:
        nm = str(e.get("name", ""))
        if e.get("type") != "file" or nm.lower() == chosen_name:
            continue
        if _is_ref_text(nm) and _room():
            got = await _download_ref(e, nm)
            if got:
                ref_files.append(got)
                total += len(got["content"])
                continue
        aux.append({"name": nm, "url": e.get("html_url") or e.get("download_url") or "", "type": "file"})

    # 2) subpastas conhecidas (references/, scripts/, …): 1 nível de arquivos de texto
    for e in entries:
        nm = str(e.get("name", ""))
        if e.get("type") != "dir" or nm.lower() not in _REF_DIRS:
            if e.get("type") == "dir":
                aux.append({"name": nm + "/", "url": e.get("html_url") or "", "type": "dir"})
            continue
        try:
            sub = await _github_dir({**gh, "path": str(e.get("path") or f"{gh.get('path','')}/{nm}").strip("/")})
        except Exception:  # noqa: BLE001
            sub = []
        for se in sub:
            sname = str(se.get("name", ""))
            rel = f"{nm}/{sname}"
            if se.get("type") == "file" and _is_ref_text(sname) and _room():
                got = await _download_ref(se, rel)
                if got:
                    ref_files.append(got)
                    total += len(got["content"])
                    continue
            if se.get("type") == "file":
                aux.append({"name": rel, "url": se.get("html_url") or se.get("download_url") or "", "type": "file"})

    return {"text": text, "aux": aux, "files": ref_files,
            "source_url": chosen.get("html_url") or raw_url(gh)}


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
    ref_files: list[dict[str, str]] = []
    source_url = url
    gh = parse_github(url)
    try:
        if gh and gh["kind"] in ("dir", "repo"):
            got = await _from_github_dir(gh)
            text, aux, source_url = got["text"], got["aux"], got["source_url"]
            ref_files = got.get("files") or []
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
        "files": ref_files,
        "source_url": source_url,
        "aux_count": len(aux),
    }


# --------------------------------------------------------------------------- #
# Import de ARQUIVO local (.skill / .zip / .md / .json) — o usuário sobe o arquivo
# --------------------------------------------------------------------------- #
_ZIP_MAGIC = b"PK\x03\x04"


def _decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def _skill_from_markdown(text: str, fallback_name: str = "skill") -> dict[str, Any]:
    """Um SKILL.md (frontmatter + corpo) → dict de skill (sem arquivos)."""
    if _looks_html(text):
        text = _html_to_text(text)
    meta, body = parse_frontmatter(text)
    name = (meta.get("name") or _title_from_markdown(body) or fallback_name).strip() or "skill"
    description = (meta.get("description") or "").strip() or f"Skill: {name}"
    return {
        "name": name[:255], "slug": slugify(meta.get("name") or name),
        "description": description[:2000], "content": body[:MAX_CONTENT],
        "files": [], "tags": [],
    }


def parse_skill_bundle(data: bytes) -> dict[str, Any]:
    """Um `.skill`/`.zip`: acha o SKILL.md e junta os arquivos de referência de TEXTO
    sob a mesma raiz (references/*, scripts/*, soltos). Binários são ignorados."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("arquivo .skill inválido (não é um zip)") from exc

    infos = [i for i in zf.infolist() if not i.is_dir()]

    def _base(n: str) -> str:
        return n.rsplit("/", 1)[-1].lower()

    skill_entry = None
    for pref in _SKILL_FILES:  # skill.md > readme.md > index.md, o mais raso vence
        cands = [i for i in infos if _base(i.filename) == pref]
        if cands:
            skill_entry = min(cands, key=lambda i: i.filename.count("/"))
            break
    if skill_entry is None:
        raise ValueError("nenhum SKILL.md/README.md dentro do .skill")

    fn = skill_entry.filename
    root = fn[: fn.rfind("/") + 1] if "/" in fn else ""
    meta, body = parse_frontmatter(_decode(zf.read(skill_entry)))
    name = (meta.get("name") or _title_from_markdown(body) or "skill").strip() or "skill"
    description = (meta.get("description") or "").strip() or f"Skill: {name}"

    files: list[dict[str, str]] = []
    total = 0
    for i in infos:
        if i is skill_entry:
            continue
        n = i.filename
        if root and not n.startswith(root):
            continue  # fora da pasta da skill
        rel = n[len(root):] if root else n
        if not rel or rel.endswith("/") or not _is_ref_text(rel):
            continue
        if i.file_size > _REF_MAX_FILE_BYTES:
            continue
        if len(files) >= _REF_MAX_FILES or total >= _REF_MAX_TOTAL_BYTES:
            break
        content = _decode(zf.read(i))[:_REF_MAX_FILE_BYTES]
        if not content.strip():
            continue
        files.append({"name": rel, "content": content})
        total += len(content)

    return {
        "name": name[:255], "slug": slugify(meta.get("name") or name),
        "description": description[:2000], "content": body[:MAX_CONTENT],
        "files": files, "tags": [],
    }


def parse_skill_upload(filename: str, data: bytes) -> list[dict[str, Any]]:
    """Dispatcher do import por arquivo: devolve uma lista de dicts de skill.
    Aceita `.skill`/`.zip` (bundle), `.json` (nosso export: objeto ou lista) e
    `.md`/`.markdown`/`.txt` ou `.skill` de texto (um SKILL.md). Levanta ValueError."""
    name_l = (filename or "").lower()
    if data[:4] == _ZIP_MAGIC or name_l.endswith(".zip"):
        return [parse_skill_bundle(data)]

    text = _decode(data).strip()
    if name_l.endswith(".json") or text[:1] in ("{", "["):
        import json
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("JSON inválido") from exc
        items = obj if isinstance(obj, list) else [obj]
        out: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = str(it.get("name") or "").strip()
            content = str(it.get("content") or "")
            if not nm and not content:
                continue
            out.append({
                "name": (nm or "skill")[:255],
                "slug": slugify(str(it.get("slug") or nm or "skill")),
                "description": str(it.get("description") or "")[:2000],
                "content": content[:MAX_CONTENT],
                "files": it.get("files") if isinstance(it.get("files"), list) else [],
                "tags": it.get("tags") if isinstance(it.get("tags"), list) else [],
            })
        if not out:
            raise ValueError("nenhuma skill encontrada no JSON")
        return out

    if not text:
        raise ValueError("arquivo vazio")
    fb = re.sub(r"\.(md|markdown|txt|skill)$", "", name_l.rsplit("/", 1)[-1]) or "skill"
    return [_skill_from_markdown(text, fallback_name=fb)]


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
