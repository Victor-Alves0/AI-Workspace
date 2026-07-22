"""Artefatos no turno: instruções ao modelo, extração da resposta e aplicação.

O modelo cria/atualiza artefatos com tags na própria resposta (funciona com
QUALQUER modelo — é só system prompt, sem tool calling):

    <artifact identifier="meu-doc" type="markdown" title="Relatório">…</artifact>

Atualização incremental (sem reenviar o conteúdo inteiro):

    <artifact-edit identifier="meu-doc">
    <<<<<<< SEARCH
    trecho exato atual
    =======
    trecho novo
    >>>>>>> REPLACE
    </artifact-edit>

Ao persistir a resposta, os blocos são extraídos para as tabelas de artefatos
(com versão) e o texto do chat fica só com um marcador [[artifact:id]], que a
UI renderiza como um cartão clicável. O conteúdo ATUAL dos artefatos do chat é
reinjetado no system prompt do turno seguinte — assim o modelo enxerga inclusive
as edições manuais do usuário.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select

from ..models import Artifact, ArtifactVersion

logger = logging.getLogger(__name__)

KINDS = {"code", "markdown", "html", "svg", "mermaid", "json", "csv", "text"}

# limites de reinjeção no contexto (por artefato / total) — protege o prompt
_MAX_PER_ARTIFACT = 24_000
_MAX_TOTAL = 80_000

_ARTIFACT_RE = re.compile(r"<artifact\s+([^>]*?)>\r?\n?(.*?)\r?\n?</artifact>", re.DOTALL)
_EDIT_RE = re.compile(r"<artifact-edit\s+([^>]*?)>\r?\n?(.*?)\r?\n?</artifact-edit>", re.DOTALL)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')

# Alguns modelos (ex.: DeepSeek) embrulham o artefato em tokens de controle
# PRÓPRIOS em vez da tag <artifact> — ex.:
#   <｜DSML｜tool artifact="true" identifier="x" title="y">…conteúdo…</｜DSML｜tool>
# A âncora é `artifact="true"` (que a nossa tag canônica NUNCA emite — ela usa
# type="…"), então normalizar isso é seguro: pareia a abertura com o próximo
# fechamento "…tool…" e reescreve como <artifact …>. Corpo vazio = só lixo → some.
_ALT_ARTIFACT_RE = re.compile(
    r'(<[^<>]*?\bartifact\s*=\s*"true"[^<>]*>)(.*?)</[^<>]*?\btool\b[^<>]*>',
    re.DOTALL | re.IGNORECASE,
)


def _normalize_alt_artifact_tags(text: str) -> str:
    if not text or 'artifact="true"' not in text:
        return text

    def _repl(m: re.Match) -> str:
        body = m.group(2)
        if not body.strip():   # tag vazia/inútil: remove o lixo da resposta
            return ""
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        keep = " ".join(
            f'{k}="{attrs[k]}"'
            for k in ("identifier", "type", "language", "title")
            if attrs.get(k)
        )
        return f"<artifact {keep}>{body}</artifact>"

    return _ALT_ARTIFACT_RE.sub(_repl, text)
_BLOCK_RE = re.compile(r"<<<<<<<\s*SEARCH\r?\n(.*?)\r?\n=======\r?\n(.*?)\r?\n>>>>>>>\s*REPLACE", re.DOTALL)

INSTRUCTIONS = """<artifacts_info>
When you produce substantial, self-contained content the user will read, edit or reuse (code files, documents, reports, emails, HTML pages, diagrams, long structured data — roughly anything over ~15 lines), put it in an ARTIFACT instead of the chat body:

<artifact identifier="kebab-case-id" type="code" language="python" title="Short title">
...complete content, no markdown fences around it...
</artifact>

- type: one of code, markdown, html, svg, mermaid, json, csv, text. `language` only for code.
- identifier: a stable slug. REUSE the exact same identifier to update that artifact (send the full new content — it becomes a new version).
- For SMALL changes to an existing artifact, prefer a partial edit (only the changed parts):

<artifact-edit identifier="same-id">
<<<<<<< SEARCH
exact existing text
=======
replacement text
>>>>>>> REPLACE
</artifact-edit>

  The SEARCH text must match the current artifact content exactly (copy it verbatim from the artifact state above). You may put several SEARCH/REPLACE blocks in one artifact-edit. If you are not sure the SEARCH matches exactly, send the full <artifact> again instead.
- Keep the conversation itself OUTSIDE the tags; after the artifact, add one short sentence saying what you created/changed.
- Do NOT use artifacts for short snippets, quick answers or explanations — those stay in the chat.
</artifacts_info>"""


def system_block(artifacts: list[Artifact]) -> str:
    """Instruções + estado ATUAL dos artefatos do chat (o modelo vê edições
    manuais do usuário e sabe quais identifiers já existem)."""
    if not artifacts:
        return INSTRUCTIONS
    parts = [INSTRUCTIONS, "\n<artifacts_state>\nCurrent artifacts in this conversation (latest content):"]
    total = 0
    for a in artifacts:
        body = a.content or ""
        if len(body) > _MAX_PER_ARTIFACT:
            body = body[:_MAX_PER_ARTIFACT] + "\n…[truncated]"
        total += len(body)
        if total > _MAX_TOTAL:
            parts.append(f'\n[{a.identifier}] "{a.title}" ({a.kind}, v{a.version}) — content omitted (too large)')
            continue
        # delimitador próprio (não a tag <artifact>): senão o modelo copia o
        # </artifact> de fechamento para dentro dos blocos SEARCH/REPLACE
        parts.append(
            f'\n--- artifact "{a.identifier}" (type={a.kind}, v{a.version}, title="{a.title}") ---\n'
            f"{body}\n"
            f'--- end of artifact "{a.identifier}" ---'
        )
    parts.append("\n</artifacts_state>")
    return "".join(parts)


def _parse_attrs(raw: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR_RE.findall(raw or "")}


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (value or "").strip().lower()).strip("-")
    return s[:80] or f"artefato-{uuid.uuid4().hex[:6]}"


def extract(content: str) -> tuple[str, list[dict[str, Any]]]:
    """Separa os blocos de artefato do texto da resposta. Retorna o texto com
    marcadores [[artifact:id]] no lugar dos blocos + a lista de operações."""
    ops: list[dict[str, Any]] = []

    def _full(m: re.Match) -> str:
        attrs = _parse_attrs(m.group(1))
        ident = _slug(attrs.get("identifier") or attrs.get("id") or attrs.get("title") or "")
        kind = (attrs.get("type") or "text").lower()
        ops.append({
            "op": "set",
            "identifier": ident,
            "kind": kind if kind in KINDS else "text",
            "language": attrs.get("language") or "",
            "title": attrs.get("title") or ident,
            "content": m.group(2),
        })
        return f"[[artifact:{ident}]]"

    def _edit(m: re.Match) -> str:
        attrs = _parse_attrs(m.group(1))
        ident = _slug(attrs.get("identifier") or attrs.get("id") or "")
        blocks = [(s, r) for s, r in _BLOCK_RE.findall(m.group(2))]
        ops.append({"op": "edit", "identifier": ident, "blocks": blocks})
        return f"[[artifact:{ident}]]"

    out = _ARTIFACT_RE.sub(_full, content)
    out = _EDIT_RE.sub(_edit, out)
    return out, ops


_TAG_LINE_RE = re.compile(r"\s*</?artifact(?:-edit)?(?:\s[^>]*)?>\s*$")
# delimitadores do <artifacts_state> ('--- artifact "x" ... ---' / '--- end of artifact ... ---')
_STATE_LINE_RE = re.compile(r"\s*-{2,}\s*(?:end of\s+)?artifact\b.*$")


def _strip_tag_lines(block: str) -> str:
    """Remove linhas que são só tags <artifact>/</artifact> ou delimitadores do
    bloco de estado, copiadas por engano para dentro de um SEARCH/REPLACE (modelos
    ancoram edições "no final" na última linha visível — que é o delimitador)."""
    lines = [
        ln
        for ln in block.replace("\r\n", "\n").split("\n")
        if not _TAG_LINE_RE.fullmatch(ln) and not _STATE_LINE_RE.fullmatch(ln)
    ]
    return "\n".join(lines)


def _find_block(text: str, search: str) -> str | None:
    """Localiza `search` em `text`. Primeiro exato; depois leniente (ignora \\r e
    espaços à direita, linha a linha) — modelos erram whitespace com frequência.
    Retorna o trecho REAL do texto a substituir (ou None)."""
    if not search:
        return None
    if search in text:
        return search
    t_lines = text.split("\n")
    s_lines = [ln.rstrip() for ln in search.replace("\r\n", "\n").split("\n")]
    while s_lines and not s_lines[0]:
        s_lines.pop(0)
    while s_lines and not s_lines[-1]:
        s_lines.pop()
    n = len(s_lines)
    if not n:
        return None
    for i in range(len(t_lines) - n + 1):
        if [ln.rstrip() for ln in t_lines[i : i + n]] == s_lines:
            return "\n".join(t_lines[i : i + n])
    return None


async def apply_ops(db, chat_id, user_id, ops: list[dict[str, Any]]) -> list[str]:
    """Aplica as operações extraídas (sessão de quem chama). Retorna os
    identifiers alterados (para o evento `artifacts` do stream)."""
    changed: list[str] = []
    for op in ops:
        ident = op["identifier"]
        row = await db.scalar(
            select(Artifact).where(Artifact.chat_id == chat_id, Artifact.identifier == ident)
        )
        if op["op"] == "set":
            if row is None:
                row = Artifact(
                    chat_id=chat_id, user_id=user_id, identifier=ident,
                    title=op["title"][:255], kind=op["kind"], language=op["language"][:40],
                    content=op["content"], version=1,
                )
                db.add(row)
                await db.flush()
            else:
                row.content = op["content"]
                row.title = op["title"][:255] or row.title
                if op["kind"] != "text":
                    row.kind = op["kind"]
                if op["language"]:
                    row.language = op["language"][:40]
                row.version += 1
            db.add(ArtifactVersion(artifact_id=row.id, version=row.version, content=row.content, label="ai"))
            changed.append(ident)
        elif op["op"] == "edit":
            if row is None:
                logger.warning("artifact-edit para artefato inexistente: %s", ident)
                continue
            text = row.content or ""
            applied = 0
            for search, replace in op["blocks"]:
                search = _strip_tag_lines(search)
                replace = _strip_tag_lines(replace)
                found = _find_block(text, search)
                if found is not None:
                    text = text.replace(found, replace, 1)
                    applied += 1
                else:
                    logger.warning("artifact-edit: SEARCH não encontrado em %s", ident)
            if applied and text != row.content:
                row.content = text
                row.version += 1
                db.add(ArtifactVersion(artifact_id=row.id, version=row.version, content=text, label="ai"))
                changed.append(ident)
    return changed


async def extract_and_apply(db, chat_id, user_id, content: str) -> tuple[str, list[str]]:
    """Atalho usado ao persistir a resposta: extrai, aplica e devolve o texto
    limpo (com marcadores) + os identifiers alterados."""
    content = _normalize_alt_artifact_tags(content or "")
    if "<artifact" not in content:
        return content, []
    clean, ops = extract(content)
    if not ops:
        return content, []
    changed = await apply_ops(db, chat_id, user_id, ops)
    return clean, changed
