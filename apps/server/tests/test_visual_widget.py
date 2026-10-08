"""Visual inline (`visual.widget.show`): o modelo escreve SVG/HTML e o chat o desenha.

Fixa o contrato com o front (`kind`/`mode`/`code`), a economia de tokens (o código não
volta ao modelo) e que o visual não é oferecido onde não há como entregá-lo (canais)."""
from __future__ import annotations

import json

from aiworkspace.chat import orchestrator as orch
from aiworkspace.integrations import channel_media
from aiworkspace.tools import effects, sift_service

SVG = '<svg viewBox="0 0 680 200"><rect x="10" y="10" width="40" height="40" fill="var(--fill-coral)"/></svg>'


def test_svg_is_detected_and_kept_whole():
    out = sift_service.widget_payload(SVG, "Crossbar")
    assert out == {"ok": True, "kind": "widget", "mode": "svg", "title": "Crossbar", "code": SVG}


def test_html_fragment_is_html_mode():
    out = sift_service.widget_payload('<div class="x">oi</div><script>1</script>')
    assert out["mode"] == "html" and out["kind"] == "widget"


def test_svg_followed_by_html_is_html_mode():
    # um <svg> com mais coisa depois não é exportável como SVG puro
    out = sift_service.widget_payload(SVG + "<p>legenda</p>")
    assert out["mode"] == "html"


def test_code_fences_are_stripped():
    out = sift_service.widget_payload("```svg\n" + SVG + "\n```")
    assert out["code"] == SVG and out["mode"] == "svg"


def test_empty_full_page_and_huge_code_are_rejected():
    assert "error" in sift_service.widget_payload("")
    assert "error" in sift_service.widget_payload("```\n```")
    assert "error" in sift_service.widget_payload("<!DOCTYPE html><html><body>x</body></html>")
    assert "error" in sift_service.widget_payload("<html><body>x</body></html>")
    big = "<div>" + "x" * sift_service.WIDGET_MAX_CHARS + "</div>"
    assert "error" in sift_service.widget_payload(big)


def test_title_is_trimmed():
    out = sift_service.widget_payload(SVG, "  " + "t" * 300)
    assert out["title"] == "t" * 120


def test_registered_as_native_visual():
    paths = {t["path"] for t in sift_service.BUILTIN_TOOLS}
    assert "visual.widget.show" in paths
    assert sift_service.tool_category("visual.widget.show") == {"category": "native"}
    assert effects.classify("visual.widget.show") == effects.READ


def test_model_gets_a_note_not_the_code_back():
    content, event = orch._shape_tool_result(sift_service.widget_payload(SVG, "x"))
    assert "<svg" not in content                       # código não volta ao modelo
    assert json.loads(content)["note"].startswith("Visual rendered")
    assert event["code"] == SVG and event["kind"] == "widget"   # a UI recebe tudo


def test_widget_is_removed_in_channels():
    ids = ["builtin:visual.widget.show", "builtin:chart.render.plot"]
    assert channel_media.unsupported_tool_ids(ids) == ["builtin:chart.render.plot"]
