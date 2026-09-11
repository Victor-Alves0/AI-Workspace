"""Saída visual dos canais: markdown → WhatsApp, gráfico → PNG, artefatos → mídia.

Antes disto: a IA chamava `chart.render.plot`, o artefato era descartado em silêncio e
ela ainda dizia "segue o gráfico abaixo"; e as tabelas/headings chegavam como canos e
cerquilhas cruas. Estes testes fixam as duas garantias."""

from __future__ import annotations

import struct
from types import SimpleNamespace
from typing import ClassVar

from aiworkspace.chart_render import render_chart
from aiworkspace.integrations import channel_media
from aiworkspace.integrations.wa_format import to_whatsapp

# ----------------------------- markdown → WhatsApp ---------------------------

def test_headings_become_bold():
    assert to_whatsapp("### Resumo dos e-mails") == "*Resumo dos e-mails*"


def test_bold_and_italic_are_converted():
    assert to_whatsapp("**forte** e __tambem__") == "*forte* e *tambem*"


def test_links_keep_the_url_visible():
    # o WhatsApp nao renderiza [texto](url) — o contato precisa VER a url
    assert to_whatsapp("veja o [relatorio](https://x.com/a)") == "veja o relatorio: https://x.com/a"


def test_table_becomes_readable_lines():
    md = (
        "| Cidade | Horario | Dia |\n"
        "|--------|---------|-----|\n"
        "| Sao Paulo | 14:43 | seg |\n"
        "| Toquio | 02:43 | ter |"
    )
    out = to_whatsapp(md)
    assert "|" not in out                       # nenhum cano sobrevive
    assert "*Sao Paulo* — Horario: 14:43 · Dia: seg" in out
    assert "*Toquio* — Horario: 02:43 · Dia: ter" in out


def test_bullets_and_rules():
    out = to_whatsapp("- um\n- dois\n\n---\n\ntexto")
    assert "• um" in out and "• dois" in out
    assert "---" not in out


def test_code_blocks_survive_untouched():
    src = "olhe:\n```\n| nao | e | tabela |\n### nem heading\n```\nfim"
    out = to_whatsapp(src)
    assert "| nao | e | tabela |" in out        # dentro do code block, nada muda
    assert "### nem heading" in out


def test_empty_is_safe():
    assert to_whatsapp("") == ""


# ------------------------------- gráfico → PNG -------------------------------

def _png_size(b: bytes) -> tuple[int, int]:
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "nao e um PNG"
    w, h = struct.unpack(">II", b[16:24])
    return w, h


def test_renders_every_chart_type():
    for t in ("line", "bar", "area", "pie"):
        png = render_chart({
            "kind": "chart", "type": t, "title": f"teste {t}",
            "labels": ["a", "b", "c"],
            "series": [{"name": "s1", "data": [3, 1, 2]}],
        })
        assert png, t
        assert _png_size(png) == (1000, 640)


def test_multi_series_renders():
    png = render_chart({
        "kind": "chart", "type": "line", "title": "duas series",
        "labels": ["jan", "fev"],
        "series": [{"name": "a", "data": [1, 2]}, {"name": "b", "data": [2, 1]}],
    })
    assert png and _png_size(png) == (1000, 640)


def test_more_series_than_colors_does_not_crash():
    # a 9a serie NAO ganha cor inventada — reusa a ordem fixa
    png = render_chart({
        "kind": "chart", "type": "bar", "title": "muitas",
        "labels": ["x"],
        "series": [{"name": f"s{i}", "data": [i + 1]} for i in range(10)],
    })
    assert png


def test_empty_series_returns_none():
    assert render_chart({"kind": "chart", "type": "line", "series": []}) is None


def test_bad_chart_does_not_raise():
    # um grafico ruim nao pode derrubar a resposta do canal
    assert render_chart({"kind": "chart", "type": "line", "series": [{"data": None}]}) is None


# --------------------------- artefatos → mídia -------------------------------

async def test_collect_turns_a_chart_event_into_png():
    events = [
        {"kind": "call", "name": "chart", "data": {}},
        {"kind": "result", "name": "chart", "data": {
            "kind": "chart", "type": "bar", "title": "Vendas",
            "labels": ["a", "b"], "series": [{"name": "s", "data": [1, 2]}],
        }},
    ]
    media = await channel_media.collect(events)
    assert len(media) == 1
    assert media[0]["mime"] == "image/png"
    assert media[0]["caption"] == "Vendas"      # o titulo vira legenda da imagem
    assert media[0]["data"][:8] == b"\x89PNG\r\n\x1a\n"


async def test_collect_ignores_non_visual_results():
    media = await channel_media.collect([
        {"kind": "result", "name": "web", "data": {"results": [1, 2]}},
        {"kind": "result", "name": "x", "data": "texto puro"},
        {"kind": "call", "name": "chart", "data": {"kind": "chart"}},  # call, nao result
    ])
    assert media == []


def test_diagram_tool_is_removed_in_channels():
    """A IA nao pode PROMETER um diagrama que o canal nao sabe entregar."""
    ids = ["builtin:web.search.query", "builtin:diagram.excalidraw.render", "builtin:chart.render.plot"]
    out = channel_media.unsupported_tool_ids(ids)
    assert "builtin:diagram.excalidraw.render" not in out
    assert "builtin:chart.render.plot" in out      # gráfico FICA: agora ele é entregue
    assert "builtin:web.search.query" in out


def test_kb_image_markdown_is_recognized():
    """O regex da extração pega URL relativa E absoluta da KB — e nada além."""
    doc = "9e0066b8-e9b5-45de-a600-445435a6c76b"
    rel = f"![foto](/knowledge/docs/{doc}/raw?t=abc.def)"
    absu = f"![x](http://192.168.1.199:8000/knowledge/docs/{doc}/raw?t=abc)"
    outra = "![y](https://exemplo.com/foto.png)"
    assert channel_media._KB_IMG_RE.search(rel)
    assert channel_media._KB_IMG_RE.search(absu)
    assert channel_media._KB_IMG_RE.search(outra) is None
    m = channel_media._KB_IMG_RE.search(rel)
    assert m.group(1) == doc and m.group(2) == "abc.def"


def test_kb_tokenless_markdown_is_recognized_for_channel_owner_resolution():
    """O modelo recebe a URL curta; o canal a autoriza pelo dono do turno."""
    doc = "9e0066b8-e9b5-45de-a600-445435a6c76b"
    m = channel_media._KB_IMG_RE.search(f"![video](/knowledge/docs/{doc}/raw)")
    assert m is not None
    assert m.group(1) == doc and m.group(2) is None


def test_kb_document_link_is_recognized():
    """Documento usa link Markdown comum, mas também precisa virar anexo no canal."""
    doc = "9e0066b8-e9b5-45de-a600-445435a6c76b"
    link = f"[manual.pdf](/knowledge/docs/{doc}/raw?t=abc.def)"
    assert channel_media._KB_IMG_RE.search(link)


async def test_split_content_media_preserves_file_order(monkeypatch):
    doc = "9e0066b8-e9b5-45de-a600-445435a6c76b"

    async def fake_doc_media(doc_id: str, token: str, user_id: str | None = None):
        assert doc_id == doc and token == "token"
        assert user_id == "owner"
        return {
            "data": b"pdf", "mime": "application/pdf", "filename": "manual.pdf",
            "caption": "",
        }

    monkeypatch.setattr(channel_media, "_kb_doc_media", fake_doc_media)
    segments = await channel_media.split_content_media(
        f"Antes\n\n[manual.pdf](/knowledge/docs/{doc}/raw?t=token)\n\nDepois",
        user_id="owner",
    )
    assert [seg["type"] for seg in segments] == ["text", "media", "text"]
    assert segments[1]["mime"] == "application/pdf"
    assert segments[1]["filename"] == "manual.pdf"


async def test_tokenless_kb_media_requires_and_scopes_to_turn_owner(monkeypatch):
    doc = "9e0066b8-e9b5-45de-a600-445435a6c76b"
    owner = "899b7395-7336-41c1-ae96-c57800627a51"
    captured = {}

    class FakeScalars:
        def first(self):
            return SimpleNamespace(data=b"video", mime="video/mp4", filename="clip.mp4")

    class FakeDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalars(self, stmt):
            captured["sql"] = str(stmt)
            captured["params"] = stmt.compile().params
            return FakeScalars()

    monkeypatch.setattr(channel_media, "SessionLocal", FakeDb)
    # Sem token e sem dono: saída do modelo não ganha acesso arbitrário ao banco.
    assert await channel_media._kb_doc_media(doc, None) is None

    item = await channel_media._kb_doc_media(doc, None, user_id=owner)
    assert item and item["data"] == b"video" and item["mime"] == "video/mp4"
    assert "knowledge_docs.user_id" in captured["sql"]
    assert owner in {str(value) for value in captured["params"].values()}


def test_sift_view_does_not_touch_the_db_object():
    """Filtrar as tools do canal nao pode PERSISTIR no modelo do usuario."""
    class FakeMC:
        tools_enabled = True
        tool_ids: ClassVar[list[str]] = [
            "builtin:diagram.excalidraw.render", "builtin:chart.render.plot",
        ]
        sift_config: ClassVar[dict] = {"mode": "prompt"}
        code_mode = True
        filter_config: ClassVar[dict] = {"tools": {}}

    mc = FakeMC()
    view = channel_media.sift_view(mc)
    assert view.tool_ids == ["builtin:chart.render.plot"]
    assert mc.tool_ids == [                       # o objeto original segue INTACTO
        "builtin:diagram.excalidraw.render", "builtin:chart.render.plot",
    ]
    assert view.code_mode is True and view.tools_enabled is True


# --------------------- janela de contexto por conexão ------------------------

def test_history_limit_default_and_missing():
    """Sem valor (None) ou ausente = padrão 40 — o comportamento antigo."""
    from types import SimpleNamespace
    assert channel_media.history_limit(SimpleNamespace(context_window=None)) == 40
    assert channel_media.history_limit(SimpleNamespace()) == 40  # atributo ausente


def test_history_limit_custom_and_all_and_cap():
    from types import SimpleNamespace
    assert channel_media.history_limit(SimpleNamespace(context_window=100)) == 100
    # 0 = "Tudo" → teto de segurança
    assert channel_media.history_limit(SimpleNamespace(context_window=0)) == channel_media.CONTEXT_WINDOW_MAX
    # acima do teto é limitado
    assert channel_media.history_limit(SimpleNamespace(context_window=99999)) == channel_media.CONTEXT_WINDOW_MAX
