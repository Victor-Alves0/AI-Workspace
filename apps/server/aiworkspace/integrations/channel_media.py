"""Artefatos visuais de um turno → mídia entregável nos canais.

O chat tem um front que desenha gráfico/diagrama/imagem. Os canais
(WhatsApp/Telegram/Discord) só sabem entregar bytes. Sem esta camada, a IA chamava
`chart.render.plot`, o artefato era DESCARTADO em silêncio, e ela ainda dizia "segue o
gráfico abaixo" — prometendo o que nunca chegava.

Aqui os `tool_events` do turno viram uma lista de mídias prontas para envio. Cada canal
só precisa saber mandar bytes (ver `send_media` de cada API).

Diagramas (excalidraw/mermaid) NÃO entram: renderizá-los exigiria um navegador
headless. Em vez de fingir, o canal simplesmente não oferece a ferramenta (ver
`unsupported_tool_ids`), então a IA não promete o que não pode cumprir.
"""

from __future__ import annotations

import logging
import re
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from ..db import SessionLocal
from ..models import GeneratedImage, KnowledgeDoc

logger = logging.getLogger(__name__)

# Ferramentas cujo resultado SÓ existe como desenho no front: nos canais elas são
# removidas do escopo do modelo (a alternativa seria a IA prometer um diagrama que
# nunca chega).
UNSUPPORTED_IN_CHANNELS = ("diagram.excalidraw.render",)


def unsupported_tool_ids(tool_ids: list[str] | None) -> list[str]:
    """Tira do modelo, NESTE turno de canal, as tools que não têm como ser entregues."""
    blocked = {f"builtin:{p}" for p in UNSUPPORTED_IN_CHANNELS}
    return [t for t in (tool_ids or []) if t not in blocked]


def sift_view(mc: Any) -> Any:
    """`model_config` "de canal": a mesma config, menos as tools sem entrega possível.

    Uma VISÃO (SimpleNamespace com o que o loader lê) em vez de mexer no objeto do
    banco — mutar `mc.tool_ids` marcaria a linha como suja e o filtro do canal
    acabaria PERSISTIDO no modelo do usuário."""
    if mc is None:
        return None
    return SimpleNamespace(
        tools_enabled=getattr(mc, "tools_enabled", False),
        tool_ids=unsupported_tool_ids(getattr(mc, "tool_ids", None)),
        sift_config=getattr(mc, "sift_config", None),
        code_mode=getattr(mc, "code_mode", False),
        filter_config=getattr(mc, "filter_config", None),
    )


# teto de seguranca da janela de contexto: "Tudo" nao pode virar tokens sem fim
# (custo/latencia e estouro do contexto do modelo). Alto, mas limitado.
CONTEXT_WINDOW_MAX = 500


def history_limit(conn: Any) -> int:
    """Quantas mensagens anteriores puxar para o histórico desta conexão de canal.

    `context_window` da conexão: None/ausente = 40 (padrão); 0 = "Tudo" (teto de
    segurança `CONTEXT_WINDOW_MAX`); N>0 = últimas N (também limitado pelo teto)."""
    cw = getattr(conn, "context_window", None)
    if cw is None:
        return 40
    if cw <= 0:
        return CONTEXT_WINDOW_MAX
    return min(cw, CONTEXT_WINDOW_MAX)


async def collect(tool_events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Varre os eventos do turno e devolve [{data, mime, filename, caption}].

    - `kind: "chart"` → renderiza o PNG no servidor (chart_render);
    - `kind: "image"` → busca os bytes da imagem gerada (o modelo já foi informado de
      que ela "foi mostrada"; nos canais é AQUI que ela de fato é entregue).
    """
    out: list[dict[str, Any]] = []
    for ev in tool_events or []:
        if ev.get("kind") != "result":
            continue
        data = ev.get("data")
        if not isinstance(data, dict):
            continue
        kind = data.get("kind")

        if kind == "chart":
            from ..chart_render import render_chart
            png = render_chart(data)
            if png:
                out.append({
                    "data": png, "mime": "image/png", "filename": "grafico.png",
                    "caption": str(data.get("title") or "")[:900],
                })

        elif kind == "image":
            img = await _image_bytes(data.get("url") or "")
            if img is not None:
                raw, mime = img
                ext = "png" if "png" in mime else "jpg"
                out.append({
                    "data": raw, "mime": mime, "filename": f"imagem.{ext}",
                    "caption": "",
                })
    return out


async def _image_bytes(url: str) -> tuple[bytes, str] | None:
    """Bytes de uma imagem gerada, a partir da URL assinada (…/images/<uuid>?sig=…).

    Vai direto ao banco: a URL é servida pelo próprio app, e fazer o servidor chamar a
    si mesmo por HTTP só adicionaria uma volta e um modo de falha."""
    image_id = _id_from_url(url)
    if image_id is None:
        return None
    try:
        async with SessionLocal() as db:
            row = (await db.scalars(
                select(GeneratedImage).where(GeneratedImage.id == image_id)
            )).first()
            if row is None or not row.data:
                return None
            return bytes(row.data), (row.mime or "image/png")
    except Exception as exc:  # noqa: BLE001 - a resposta em texto não pode cair por isso
        logger.warning("canal: falha ao carregar a imagem gerada (%s): %s", image_id, exc)
        return None


def _id_from_url(url: str) -> uuid.UUID | None:
    for part in (url or "").split("?")[0].split("/"):
        try:
            return uuid.UUID(part)
        except (ValueError, AttributeError):
            continue
    return None


# Arquivo da Base de Conhecimento embutido na resposta como markdown
# (`![imagem](/knowledge/docs/<id>/raw?t=<token>)` ou
# `[arquivo](/knowledge/docs/<id>/raw)`). No chat o front renderiza/abre;
# no canal o contato receberia um LINK local inútil — aqui ele vira mídia de verdade.
# O `!` é OPCIONAL: modelos às vezes degradam o `![img](url)` para um link `[img](url)`
# (o prompt do canal proíbe links) — sem tolerar isso, a imagem sumia e o `[img](link)`
# vazava como texto. Aceitamos ambos e sempre entregamos a mídia.
_KB_IMG_RE = re.compile(
    r"!?\[[^\]\n]*\]\((?:https?://[^/\s)]+)?/knowledge/docs/([0-9a-fA-F-]{36})/raw"
    r"(?:\?t=([^\s)]+))?\)"
)


async def _kb_doc_media(
    doc_id: str, token: str | None, user_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve um arquivo da KB sem confiar na saída do modelo.

    Links finais assinados são validados como antes. O modelo, porém, recebe links
    curtos sem token para economizar contexto; nesses casos o canal só pode resolver
    o arquivo quando conhece o usuário dono do turno e a query é escopada por ele.
    """
    from ..knowledge.links import verify_doc_token
    signed = bool(token) and verify_doc_token(doc_id, token or "")
    try:
        parsed_doc_id = uuid.UUID(doc_id)
        parsed_user_id = uuid.UUID(user_id) if user_id else None
    except (ValueError, TypeError, AttributeError):
        return None
    if not signed and parsed_user_id is None:
        return None
    try:
        async with SessionLocal() as db:
            stmt = select(KnowledgeDoc).where(KnowledgeDoc.id == parsed_doc_id)
            # Quando há usuário do turno, escopamos SEMPRE por ele — até um link
            # assinado copiado de outra conta não pode atravessar o canal atual.
            if parsed_user_id is not None:
                stmt = stmt.where(KnowledgeDoc.user_id == parsed_user_id)
            d = (await db.scalars(stmt)).first()
            if d is None or not d.data:
                return None
            mime = d.mime or "application/octet-stream"
            return {"data": bytes(d.data), "mime": mime,
                    "filename": d.filename or "arquivo", "caption": ""}
    except Exception as exc:  # noqa: BLE001 - o texto segue mesmo sem a mídia
        logger.warning("canal: falha ao carregar mídia da KB (%s): %s", doc_id, exc)
        return None


async def extract_content_images(
    text: str, user_id: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Extrai os arquivos da KB do texto da resposta → (texto sem os markdowns,
    [{data, mime, filename, caption}]). Para canais que entregam texto e mídia em
    blocos separados (o WhatsApp usa `split_content_media`, que preserva a ordem)."""
    media: list[dict[str, Any]] = []
    out = text or ""
    for m in _KB_IMG_RE.finditer(text or ""):
        item = await _kb_doc_media(m.group(1), m.group(2), user_id=user_id)
        if item is None:
            continue
        media.append(item)
        out = out.replace(m.group(0), "")
    return (re.sub(r"\n{3,}", "\n\n", out).strip(), media) if media else (text or "", media)


async def split_content_media(
    text: str, user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Divide a resposta em segmentos ORDENADOS para entregar "texto, imagem, texto"
    na ordem em que a mídia aparece — em vez de todo o texto e depois toda a mídia.

    Retorna uma lista de `{"type":"text","text":...}` e
    `{"type":"media","data","mime","filename","caption"}`. Markdown de imagem da KB
    inválido/não resolvido fica no texto (o `wa_format` depois limpa o resíduo)."""
    src = text or ""
    segs: list[dict[str, Any]] = []
    pos = 0
    for m in _KB_IMG_RE.finditer(src):
        item = await _kb_doc_media(m.group(1), m.group(2), user_id=user_id)
        if item is None:
            continue  # não resolveu → deixa o markdown no texto, não corta
        before = src[pos:m.start()]
        if before.strip():
            segs.append({"type": "text", "text": before})
        segs.append({"type": "media", **item})
        pos = m.end()
    tail = src[pos:]
    if tail.strip() or not segs:
        segs.append({"type": "text", "text": tail})
    return segs
