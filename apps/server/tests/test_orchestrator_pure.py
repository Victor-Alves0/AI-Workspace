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


def test_shape_skill_proposal_note():
    """Proposta de skill: o modelo recebe só a nota (não pode afirmar que salvou);
    o card completo (editável) vai à UI."""
    result = {"kind": "skill_proposal", "proposal_id": "p1", "slug": "s",
              "name": "N", "description": "d", "content": "# passos", "tags": []}
    content, event = orch._shape_tool_result(result)
    assert "Do NOT claim the skill was saved" in content
    assert "# passos" not in content  # o conteúdo não volta ao modelo
    assert event["kind"] == "skill_proposal" and event["content"] == "# passos"


def test_shape_brain_note_note():
    result = {"kind": "brain_note", "doc_id": "d", "title": "Decisões",
              "action": "updated", "preview": "p", "url": "/s/d"}
    content, event = orch._shape_tool_result(result)
    parsed = json.loads(content)
    assert parsed["ok"] is True and "Decisões" in parsed["note"] and "updated" in parsed["note"]
    assert event["kind"] == "brain_note" and event["url"] == "/s/d"


def test_shape_knowledge_splits_model_vs_ui():
    """Conhecimento: modelo recebe os TRECHOS (_model); a UI só as fontes."""
    result = {"kind": "knowledge", "sources": [{"n": 1}], "_model": "trecho X", "count": 1}
    content, event = orch._shape_tool_result(result)
    assert content == "trecho X"
    assert "_model" not in event  # o texto pesado não incha o tool_events salvo
    assert event["sources"] == [{"n": 1}]


# ----------------------- retenção de resultados de tools ----------------------

def test_tool_result_context_budget_keeps_recent_full_results():
    """Ao exceder o orçamento, só resultados antigos do prompt são reduzidos.

    A cópia original usada como evento/persistência não passa por esta função; aqui
    validamos a cópia temporária enviada à próxima iteração do modelo.
    """
    old = "INICIO-" + ("a" * 9_000) + "-FIM"
    recent_a = "RECENTE-A-" + ("b" * 30_000)
    recent_b = "RECENTE-B-" + ("c" * 30_000)
    messages = [
        {"role": "tool", "tool_call_id": "old", "content": old},
        {"role": "tool", "tool_call_id": "a", "content": recent_a},
        {"role": "tool", "tool_call_id": "b", "content": recent_b},
    ]

    changed = orch._trim_tool_results(
        messages, keep_last=2, max_chars=1_000, total_limit_chars=60_000,
    )

    assert changed == 1
    assert messages[1]["content"] == recent_a
    assert messages[2]["content"] == recent_b
    excerpt = messages[0]["content"]
    assert "orçamento de contexto" in excerpt
    assert excerpt.startswith("INICIO-") and excerpt.endswith("-FIM")
    assert len(excerpt) <= 1_000


def test_tool_result_context_budget_does_not_reduce_without_pressure():
    original = "resultado útil " * 1_000
    messages = [
        {"role": "tool", "tool_call_id": "old", "content": original},
        {"role": "tool", "tool_call_id": "recent", "content": "recente"},
    ]

    changed = orch._trim_tool_results(
        messages, keep_last=1, max_chars=100, total_limit_chars=50_000,
    )

    assert changed == 0
    assert messages[0]["content"] == original


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


def test_web_research_workflow_requires_reading_the_result_page():
    """O modelo recebe uma regra explícita para não confundir snippet com fonte."""
    assert "web.search.query" in orch.WEB_RESEARCH_WORKFLOW
    assert "web.page.read" in orch.WEB_RESEARCH_WORKFLOW
    assert "snippet" in orch.WEB_RESEARCH_WORKFLOW.lower()


# ----------------------------- _finalize_usage --------------------------------

def test_merge_usage_accepts_flat_responses_metrics():
    """O adaptador da Responses API expõe reasoning/cache no topo do usage."""
    total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
    }
    orch._merge_usage(total, {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
        "reasoning_tokens": 5,
        "cached_tokens": 7,
    })
    assert total["reasoning_tokens"] == 5
    assert total["cached_tokens"] == 7


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


# --------------------- _salvage_leaked_tool_calls (vazamentos) ----------------

# Bloco Harmony (OpenAI/GPT-5.x) vazado como texto: `to=[functions.]NOME <constraint> {json}`.
# Os tokens especiais chegam CORROMPIDOS em mojibake (`代json`, `તર不中返`) — ancoramos no
# `to=…` + JSON, não neles. Reproduz o vazamento real observado no chat do Metabase (Luna).
_HARMONY_LEAK = (
    "Vou concluir as confirmações.\n\n"
    " to=functions.code__exec__run 代json\n"
    '{"command":"./bin/test-agent :only \'[metabase.session.api-test]\'","confirm":false}તર不中返\n\n'
    " to=code__exec__run 代json\n"
    '{"command":"echo {a: \\"has } brace\\"}","setup":false}\n\n'
    " to=functions.code__files__browse 代json\n"
    '{"action":"search","query":":http","depth":7}\n\n'
    "Concluí a investigação.\n"
)


def test_salvage_harmony_multi_call():
    """3 blocos Harmony → 3 tool_calls; o `functions.` é retirado do nome e o objeto
    JSON é extraído por casamento de chaves (um `}` DENTRO de string não corta o args)."""
    calls = orch._salvage_leaked_tool_calls(_HARMONY_LEAK)
    assert [c["function"]["name"] for c in calls] == [
        "code__exec__run", "code__exec__run", "code__files__browse",
    ]
    # o call do meio tem `{`/`}` dentro do valor: o args precisa vir íntegro
    mid = json.loads(calls[1]["function"]["arguments"])
    assert mid["command"] == 'echo {a: "has } brace"}'


def test_salvage_harmony_marker_inside_arg_not_double_counted():
    """Um `to=functions.x` DENTRO de uma string de argumento não vira um call extra."""
    leaked = ' to=functions.code__exec__run x\n{"command":"grep \'to=functions.evil\' f"}\n'
    calls = orch._salvage_leaked_tool_calls(leaked)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "code__exec__run"


def test_salvage_no_false_positive_on_prose():
    """Relatório de segurança citando nomes de tools/`dangerouslySetInnerHTML` NÃO é
    confundido com vazamento (nenhum marcador Harmony/DeepSeek/Hermes presente)."""
    report = (
        "O executor chama code.exec.run e usa http/request; recomendo escapar antes de "
        "dangerouslySetInnerHTML. Nenhuma cadeia foi confirmada."
    )
    assert orch._salvage_leaked_tool_calls(report) == []
    assert orch._LEAK_START_RE.search(report) is None


def test_salvage_harmony_detected_by_leak_start_re():
    """O marcador `to=functions.` liga a supressão no stream (tier soft = só com tools)."""
    m = orch._LEAK_START_RE.search(_HARMONY_LEAK)
    assert m is not None and m.group(0) == "to=functions."


def test_salvage_hermes_still_works():
    """Regressão: os formatos antigos continuam resgatando."""
    hermes = '<tool_call>{"name":"search_tools","arguments":{"q":"x"}}</tool_call>'
    calls = orch._salvage_leaked_tool_calls(hermes)
    assert [c["function"]["name"] for c in calls] == ["search_tools"]


def test_strip_leaked_markup_removes_harmony_from_history():
    """Sanitização do histórico corta o bloco Harmony vazado (senão o modelo o imita)."""
    cleaned = orch._strip_leaked_markup(_HARMONY_LEAK)
    assert "to=functions" not in cleaned
    assert cleaned.startswith("Vou concluir")
