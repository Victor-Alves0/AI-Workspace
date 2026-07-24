"""Importação de skills/prompts a partir de um link (autoensinamento).

Hermético: a costura de HTTP (`_get`) é substituída — nenhuma rede. Cobre a
resolução de URL do GitHub, o parsing de frontmatter, a decisão de escopo
(só o SKILL.md + auxiliares listados) e os DOIS formatos de identificador.
"""

from __future__ import annotations

import pytest

from aiworkspace.integrations import library_import as li

SKILL_MD = """---
name: Code Review
description: Use when reviewing a pull request or a diff.
---

# Code Review

1. Read the diff.
2. Check the tests.
"""


# --------------------------------------------------------------------------- #
# Frontmatter
# --------------------------------------------------------------------------- #
def test_parse_frontmatter_extracts_meta_and_body():
    meta, body = li.parse_frontmatter(SKILL_MD)
    assert meta["name"] == "Code Review"
    assert meta["description"].startswith("Use when reviewing")
    assert body.startswith("# Code Review")
    assert "---" not in body


def test_parse_frontmatter_without_frontmatter_returns_whole_body():
    meta, body = li.parse_frontmatter("# Só um título\n\ntexto")
    assert meta == {}
    assert body.startswith("# Só um título")


def test_parse_frontmatter_ignores_nested_and_lists():
    meta, _ = li.parse_frontmatter(
        "---\nname: X\nmetadata:\n  type: y\n- item\n---\ncorpo\n"
    )
    assert meta == {"name": "X", "metadata": ""} or meta == {"name": "X"}
    assert "type" not in meta  # chave aninhada não vira metadado de 1º nível


# --------------------------------------------------------------------------- #
# Identificadores: os dois formatos do backend
# --------------------------------------------------------------------------- #
def test_slugify_uses_underscore_for_skills_and_hyphen_for_prompts():
    # Skill: ^[a-z0-9][a-z0-9_]*$   |   Prompt: ^[a-z0-9][a-z0-9-]*$
    assert li.slugify("Code Review!") == "code_review"
    assert li.slugify("Code Review!", sep="-") == "code-review"


def test_slugify_falls_back_and_trims():
    assert li.slugify("!!!", "skill") == "skill"
    assert not li.slugify("  Olá Mundo  ").startswith("_")


# --------------------------------------------------------------------------- #
# URLs do GitHub
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,kind,path", [
    ("https://github.com/o/r/blob/main/skills/x/SKILL.md", "file", "skills/x/SKILL.md"),
    ("https://github.com/o/r/tree/main/skills/x", "dir", "skills/x"),
    ("https://github.com/o/r", "repo", ""),
    ("https://raw.githubusercontent.com/o/r/main/SKILL.md", "file", "SKILL.md"),
])
def test_parse_github_shapes(url, kind, path):
    gh = li.parse_github(url)
    assert gh is not None
    assert gh["kind"] == kind and gh["path"] == path
    assert gh["owner"] == "o" and gh["repo"] == "r"


def test_parse_github_ignores_other_hosts():
    assert li.parse_github("https://example.com/a/b") is None


def test_raw_url_builds_raw_githubusercontent():
    gh = li.parse_github("https://github.com/o/r/blob/main/a/SKILL.md")
    assert li.raw_url(gh) == "https://raw.githubusercontent.com/o/r/main/a/SKILL.md"


# --------------------------------------------------------------------------- #
# fetch_skill
# --------------------------------------------------------------------------- #
async def test_fetch_skill_from_raw_markdown(monkeypatch):
    async def fake_get(url, as_json=False):
        assert "raw.githubusercontent.com" in url
        return SKILL_MD

    monkeypatch.setattr(li, "_get", fake_get)
    out = await li.fetch_skill("https://github.com/o/r/blob/main/SKILL.md")
    assert out["ok"] is True
    assert out["name"] == "Code Review"
    assert out["slug"] == "code_review"
    assert out["description"].startswith("Use when reviewing")
    assert "Read the diff" in out["content"]
    assert out["aux_count"] == 0


async def test_fetch_skill_from_github_folder_lists_aux_without_downloading(monkeypatch):
    """Decisão de escopo: importa SÓ o SKILL.md; scripts/references viram uma LISTA."""
    downloaded: list[str] = []

    async def fake_get(url, as_json=False):
        if as_json:
            return [
                {"name": "SKILL.md", "type": "file",
                 "download_url": "https://raw/x/SKILL.md", "html_url": "https://gh/x/SKILL.md"},
                {"name": "scripts", "type": "dir", "html_url": "https://gh/x/scripts"},
                {"name": "reference.md", "type": "file",
                 "download_url": "https://raw/x/reference.md", "html_url": "https://gh/x/reference.md"},
            ]
        downloaded.append(url)
        return SKILL_MD

    monkeypatch.setattr(li, "_get", fake_get)
    out = await li.fetch_skill("https://github.com/o/r/tree/main/skills/x")
    assert out["ok"] is True and out["aux_count"] == 2
    # só o SKILL.md foi baixado
    assert downloaded == ["https://raw/x/SKILL.md"]
    # os auxiliares aparecem listados no conteúdo, com link
    assert "scripts" in out["content"] and "reference.md" in out["content"]
    assert "https://gh/x/scripts" in out["content"]


async def test_fetch_skill_folder_without_skill_md_errors(monkeypatch):
    async def fake_get(url, as_json=False):
        return [{"name": "notes.txt", "type": "file"}] if as_json else ""

    monkeypatch.setattr(li, "_get", fake_get)
    out = await li.fetch_skill("https://github.com/o/r/tree/main/x")
    assert "error" in out and "SKILL.md" in out["error"]


async def test_fetch_skill_falls_back_to_h1_when_no_frontmatter(monkeypatch):
    async def fake_get(url, as_json=False):
        return "# Minha Skill\n\npasso a passo"

    monkeypatch.setattr(li, "_get", fake_get)
    out = await li.fetch_skill("https://example.com/skill.md")
    assert out["name"] == "Minha Skill"
    assert out["description"]  # sempre preenche algo (o modelo exige)


async def test_fetch_skill_strips_html(monkeypatch):
    async def fake_get(url, as_json=False):
        return "<!doctype html><html><body><h1>Oi</h1><p>conteudo</p></body></html>"

    monkeypatch.setattr(li, "_get", fake_get)
    out = await li.fetch_skill("https://example.com/page")
    assert out["ok"] is True
    assert "<p>" not in out["content"] and "conteudo" in out["content"]


async def test_fetch_skill_empty_is_error(monkeypatch):
    async def fake_get(url, as_json=False):
        return "   "

    monkeypatch.setattr(li, "_get", fake_get)
    assert "error" in await li.fetch_skill("https://example.com/x")


# --------------------------------------------------------------------------- #
# fetch_text (prompts)
# --------------------------------------------------------------------------- #
async def test_fetch_text_returns_title_and_body(monkeypatch):
    async def fake_get(url, as_json=False):
        return SKILL_MD

    monkeypatch.setattr(li, "_get", fake_get)
    out = await li.fetch_text("https://example.com/p.md")
    assert out["ok"] is True
    assert out["title"] == "Code Review"        # do frontmatter (name)
    assert out["content"].startswith("# Code Review")


# --------------------------------------------------------------------------- #
# Registro das tools
# --------------------------------------------------------------------------- #
def test_tools_registered_as_native():
    from aiworkspace.tools import sift_service

    paths = {t["path"] for t in sift_service.BUILTIN_TOOLS}
    assert "skills.library.manage" in paths
    assert "prompts.library.manage" in paths
    for p in ("skills.library.manage", "prompts.library.manage"):
        assert sift_service.tool_category(p)["category"] == "native"
