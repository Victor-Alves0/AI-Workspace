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


# Imagem da Base de Conhecimento embutida na resposta como markdown
# (`![nome](/knowledge/docs/<id>/raw?t=<token>)`). No chat o front renderiza; no
# canal o contato receberia um LINK local inútil — aqui ela vira mídia de verdade.
_KB_IMG_RE = re.compile(
    r"!\[[^\]\n]*\]\((?:https?://[^/\s)]+)?/knowledge/docs/([0-9a-fA-F-]{36})/raw\?t=([^\s)]+)\)"
)


async def extract_content_images(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Extrai as imagens da KB do texto da resposta → (texto sem os markdowns,
    [{data, mime, filename, caption}]). Valida o token assinado de cada URL (a
    resposta do modelo não é confiável) e lê os bytes direto do banco."""
    from ..knowledge.links import verify_doc_token

    media: list[dict[str, Any]] = []
    out = text or ""
    for m in _KB_IMG_RE.finditer(text or ""):
        doc_id, token = m.group(1), m.group(2)
        if not verify_doc_token(doc_id, token):
            continue
        try:
            async with SessionLocal() as db:
                d = await db.get(KnowledgeDoc, uuid.UUID(doc_id))
                if d is None or not d.data or not (d.mime or "").startswith("image/"):
                    continue
                media.append({
                    "data": bytes(d.data), "mime": d.mime,
                    "filename": d.filename or "imagem", "caption": "",
                })
        except Exception as exc:  # noqa: BLE001 - o texto segue mesmo sem a imagem
            logger.warning("canal: falha ao carregar imagem da KB (%s): %s", doc_id, exc)
            continue
        out = out.replace(m.group(0), "")
    return (re.sub(r"\n{3,}", "\n\n", out).strip(), media) if media else (text or "", media)
