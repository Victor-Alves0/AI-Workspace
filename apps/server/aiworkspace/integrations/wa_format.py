"""Markdown → texto de WhatsApp.

O prompt PEDE ao modelo que não use markdown, mas pedir não é garantir: ele escapa com
`### Título`, tabelas `| a | b |` e `[texto](url)` — e o WhatsApp não renderiza nada
disso, então o contato recebe os canos e as cerquilhas crus. Esta é a rede de proteção:
roda na saída, independente de o modelo ter obedecido.

O WhatsApp só entende *negrito*, _itálico_, ~tachado~ e ```código```.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$")
_BOLD_MD = re.compile(r"\*\*(.+?)\*\*", re.S)
_BOLD_ALT = re.compile(r"__(.+?)__", re.S)
_ITALIC_MD = re.compile(r"(?<![\*\w])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![\*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s\)]+)\)")
# rede final: markdown de imagem/link ÓRFÃO (url relativa, placeholder, token quebrado)
# que escapou das regras acima. Sem isto, um `[bunny](link)` que o modelo escreveu no
# lugar da imagem vazava cru pro contato. Vira só o rótulo — nada de canos/parênteses.
_IMAGE_ANY = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
_LINK_ANY = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
_BULLET = re.compile(r"(?m)^\s*[-*+]\s+")
_HRULE = re.compile(r"(?m)^\s*([-*_])\s*\1\s*\1[\s\-*_]*$")
_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_SEP_ROW = re.compile(r"^[\s\|:\-]+$")
_MULTI_NL = re.compile(r"\n{3,}")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_to_lines(rows: list[str]) -> list[str]:
    """Tabela markdown → lista legível.

    Uma tabela é uma grade; o WhatsApp não tem grade. Vira um registro por linha, com
    o cabeçalho virando rótulo de cada campo — que é como a informação seria dita.
    """
    parsed = [_cells(r) for r in rows if not _SEP_ROW.match(r)]
    if not parsed:
        return []
    if len(parsed) == 1:  # só cabeçalho: nada a dizer além dele
        return [" · ".join(parsed[0])]
    head, body = parsed[0], parsed[1:]
    out: list[str] = []
    for row in body:
        # 1ª coluna é o "assunto" da linha (vai em negrito); as demais viram "rótulo: valor"
        title = row[0] if row else ""
        parts = [
            f"{head[i]}: {row[i]}"
            for i in range(1, min(len(head), len(row)))
            if row[i]
        ]
        line = f"*{title}*" if title else ""
        if parts:
            line = f"{line} — {' · '.join(parts)}" if line else " · ".join(parts)
        if line:
            out.append(line)
    return out


def to_whatsapp(text: str) -> str:
    """Converte a resposta do modelo para o que o WhatsApp de fato renderiza."""
    if not (text or "").strip():
        return text or ""

    # blocos de código ``` ``` são preservados intactos (o WhatsApp os renderiza)
    chunks = (text or "").split("```")
    for i in range(0, len(chunks), 2):  # só os índices PARES estão fora do código
        s = chunks[i]
        s = _IMAGE.sub(r"\2", s)                  # imagem markdown (url http) → a própria URL
        s = _LINK.sub(r"\1: \2", s)               # [texto](url http) → texto: url
        s = _IMAGE_ANY.sub(r"\1", s)              # imagem órfã/relativa → só o rótulo
        s = _LINK_ANY.sub(r"\1", s)               # link órfão/relativo → só o texto
        # ITÁLICO ANTES do negrito: negrito vira `*x*`, que é exatamente a cara do
        # itálico em markdown — invertendo a ordem, todo negrito viraria itálico. A
        # lookbehind do regex já o impede de casar os asteriscos internos de `**x**`.
        s = _ITALIC_MD.sub(r"_\1_", s)            # *x* solto → _x_
        s = _HEADING.sub(r"*\1*", s)              # ### Título → *Título*
        s = _BOLD_MD.sub(r"*\1*", s)              # **x** → *x*
        s = _BOLD_ALT.sub(r"*\1*", s)             # __x__ → *x*
        s = _HRULE.sub("", s)                     # --- (régua) → nada

        # tabelas: agrupa linhas contíguas que começam e terminam com "|"
        lines, out, buf = s.split("\n"), [], []
        for ln in lines:
            if _ROW.match(ln):
                buf.append(ln)
                continue
            if buf:
                out.extend(_table_to_lines(buf))
                buf = []
            out.append(ln)
        if buf:
            out.extend(_table_to_lines(buf))
        s = "\n".join(out)

        s = _BULLET.sub("• ", s)                  # "- item" → "• item"
        chunks[i] = _MULTI_NL.sub("\n\n", s)

    return "```".join(chunks).strip()
