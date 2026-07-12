"""Testes das regras da suíte de DECISÃO de tools (playground/runner._apply_rule)."""
from __future__ import annotations

from aiworkspace.playground.runner import _apply_rule


# ------------------------------- texto ---------------------------------------

def test_contains():
    assert _apply_rule({"mode": "contains", "value": "PETR4"}, "cotação de PETR4 hoje") is True
    assert _apply_rule({"mode": "contains", "value": "xyz"}, "cotação") is False


def test_regex():
    assert _apply_rule({"mode": "regex", "value": r"R\$\s?\d+"}, "custa R$ 22") is True
    assert _apply_rule({"mode": "regex", "value": "("}, "qualquer") is None  # regex inválida


def test_none_is_skip():
    assert _apply_rule({"mode": "none", "value": ""}, "qualquer coisa") is None
    assert _apply_rule(None, "x") is None


# ------------------------------ decisão --------------------------------------

def test_tool_called_by_name():
    r = _apply_rule({"mode": "tool_called", "value": "web.search.query"},
                    "", tools_used=["search_tools", "web.search.query"])
    assert r is True


def test_tool_called_normalizes_dot_and_underscore():
    """'research.deep.run' casa com o nome flat 'research__deep__run'."""
    r = _apply_rule({"mode": "tool_called", "value": "research.deep.run"},
                    "", tools_used=["research__deep__run"])
    assert r is True


def test_tool_called_via_run_code_blob():
    """Em code mode a tool real é chamada DENTRO do run_code — casa pelo blob."""
    r = _apply_rule({"mode": "tool_called", "value": "web.search.query"},
                    "", tools_used=["run_code"],
                    tool_blobs='output = call("web.search.query", q="dolar")')
    assert r is True


def test_tool_not_called():
    r = _apply_rule({"mode": "tool_not_called", "value": "research.deep.run"},
                    "", tools_used=["run_code", "web.search.query"])
    assert r is True  # deep NÃO foi chamada → passa
    r2 = _apply_rule({"mode": "tool_not_called", "value": "research.deep.run"},
                     "", tools_used=["research__deep__run"])
    assert r2 is False  # foi chamada → falha


def test_no_tool_search_tools_does_not_count():
    """search_tools/descoberta não conta como tool real (decidir NÃO usar é ok)."""
    assert _apply_rule({"mode": "no_tool", "value": ""}, "haiku", tools_used=[]) is True
    assert _apply_rule({"mode": "no_tool", "value": ""}, "haiku",
                       tools_used=["search_tools"]) is True
    assert _apply_rule({"mode": "no_tool", "value": ""}, "resp",
                       tools_used=["web.search.query"]) is False
