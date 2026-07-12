"""Testes puros (sem rede/DB) das funções de formatação/uso do orchestrator."""
from __future__ import annotations

import json

from aiworkspace.chat import orchestrator as orch


# --------------------------- _shape_tool_result -------------------------------

def test_shape_str_json_roundtrip():
    """String JSON não é re-serializada (evita duplo-encode); o dict vai à UI."""
    raw = json.dumps({"results": [{"title": "x"}]}, ensure_ascii=False)
    content, event = orch._shape_tool_result(raw)
    assert content == raw  # não re-encodou
    assert event == {"results": [{"title": "x"}]}


def test_shape_str_plain_text():
    """search_tools devolve texto puro (não-JSON): passa cru nos dois lados."""
    content, event = orch._shape_tool_result("- web.search — busca")
    assert content == "- web.search — busca"
    assert event == "- web.search — busca"


def test_shape_dict_serialized_for_model():
    content, event = orch._shape_tool_result({"ok": True, "n": 3})
    assert json.loads(content) == {"ok": True, "n": 3}
    assert event == {"ok": True, "n": 3}


def test_shape_image_hides_url_from_model():
    """Imagem: o modelo recebe só uma nota; a UI recebe o dict com a URL."""
    result = {"kind": "image", "url": "https://x/y.png", "prompt": "gato"}
    content, event = orch._shape_tool_result(result)
    assert "y.png" not in content  # URL NÃO vai ao modelo
    assert json.loads(content)["note"].startswith("Image generated")
    assert event["url"] == "https://x/y.png"  # UI mantém a URL


def test_shape_email_draft_note():
    content, event = orch._shape_tool_result({"kind": "email_draft", "to": "a@b.c"})
    assert "Do NOT claim the email was sent" in content
    assert event["kind"] == "email_draft"


def test_shape_knowledge_splits_model_vs_ui():
    """Conhecimento: modelo recebe os TRECHOS (_model); a UI só as fontes."""
    result = {"kind": "knowledge", "sources": [{"n": 1}], "_model": "trecho X", "count": 1}
    content, event = orch._shape_tool_result(result)
    assert content == "trecho X"
    assert "_model" not in event  # o texto pesado não incha o tool_events salvo
    assert event["sources"] == [{"n": 1}]


# --------------------------- _compose_tool_prompt -----------------------------

def test_compose_prompt_mode_prompt_no_catalog():
    """Modo 'prompt' (premissa SIFT): NÃO enumera o catálogo."""
    out = orch._compose_tool_prompt("BASE", ["Tool A — faz X"], "prompt", "custom", "execute_tool")
    assert "Tool A — faz X" not in out
    assert "custom" in out
    assert orch.TOOL_ACTION_GUARD in out


def test_compose_prompt_mode_list_enumerates():
    out = orch._compose_tool_prompt("BASE", ["Tool A — faz X"], "list", "custom", "execute_tool")
    assert "Tool A — faz X" in out  # modo list injeta o catálogo


# ----------------------------- _finalize_usage --------------------------------

def test_finalize_usage_input_breakdown_proportional():
    total = {"prompt_tokens": 100, "completion_tokens": 10, "reasoning_tokens": 0}
    orch._finalize_usage(
        total, input_chars={"user": 30, "tools": 70}, tool_result_chars={},
        extra_breakdown=None, reasoning_text="",
    )
    ib = total["input_breakdown"]
    assert ib["user"] + ib["tools"] == 100  # soma bate com o total real
    assert ib["tools"] > ib["user"]  # proporcional ao tamanho


def test_finalize_usage_reasoning_estimate_when_missing():
    """Sem reasoning_tokens do provedor, estima pelo texto capturado (~len/4)."""
    total = {"prompt_tokens": 10, "completion_tokens": 50, "reasoning_tokens": 0}
    orch._finalize_usage(
        total, input_chars={"user": 10}, tool_result_chars={},
        extra_breakdown=None, reasoning_text="x" * 40,
    )
    assert total["output_breakdown"]["thinking"] == 10  # 40/4
    assert total["output_breakdown"]["output"] == 40


def test_finalize_usage_zero_tokens_no_div_by_zero():
    total = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    orch._finalize_usage(total, input_chars={"user": 0}, tool_result_chars={},
                         extra_breakdown=None, reasoning_text="")
    assert total["input_breakdown"]["user"] == 0


# ----------------------------- guard detection --------------------------------

def test_guard_refusal_opener_matches():
    g = {"detect": "refusal"}
    assert orch._guard_triggered(g, "Desculpe, mas não posso ajudar com isso.", None)


def test_guard_refusal_veto_false_positive():
    """'I can't wait to help' NÃO é recusa (continuação positiva veta)."""
    g = {"detect": "refusal"}
    assert not orch._guard_triggered(g, "I can't wait to help you!", None)


def test_guard_empty_detects_blank():
    g = {"detect": "empty"}
    assert orch._guard_triggered(g, "   ", None)
    assert not orch._guard_triggered(g, "resposta real", None)


def test_guard_regex():
    g = {"detect": "regex", "pattern": r"\bLGPD\b"}
    assert orch._guard_triggered(g, "viola a LGPD", None)
    assert not orch._guard_triggered(g, "tudo certo", None)
