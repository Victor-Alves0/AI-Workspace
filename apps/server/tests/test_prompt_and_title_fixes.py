"""Regressões da rodada de bugs: título vazio em modelos de raciocínio, aviso das
tools NATIVAS (fora do índice SIFT) e o `limit` do search_knowledge.

Hermético: só inspeciona constantes/specs e o texto dos prompts — sem rede nem DB.
"""

from __future__ import annotations

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat import titles


# --------------------------------------------------------------------------- #
# Título (item 1)
# --------------------------------------------------------------------------- #
def test_title_budget_survives_reasoning_models():
    """Com 32 tokens o reasoning consome tudo e o `content` volta vazio — o chat
    ficava com a 1ª mensagem crua (uma URL do YouTube) como título."""
    assert titles.TITLE_MAX_TOKENS >= 256


def test_clean_strips_quotes_and_prefix():
    assert titles._clean('"Prova de 1=2"') == "Prova de 1=2"
    assert titles._clean("Title: Prova de 1=2") == "Prova de 1=2"
    assert titles._clean("") == ""


# --------------------------------------------------------------------------- #
# Tools nativas fora do SIFT (item 4)
# --------------------------------------------------------------------------- #
def test_native_tools_note_lists_names_and_warns_about_search_tools():
    note = orch._native_tools_note(["search_knowledge", "generate_image"])
    assert "`search_knowledge`" in note and "`generate_image`" in note
    # o ponto do aviso: search_tools NÃO indexa essas tools
    assert "search_tools" in note and "does NOT index" in note


def test_native_tools_note_mentions_user_owned_media():
    """O bug real: pediram uma FOTO da base e o modelo negou tendo a tool na mão."""
    note = orch._native_tools_note(["search_knowledge"])
    assert "search_knowledge" in note
    assert "photo" in note.lower()


# --------------------------------------------------------------------------- #
# search_knowledge (item 4)
# --------------------------------------------------------------------------- #
def test_search_knowledge_exposes_limit():
    spec = orch._search_knowledge_tool()
    props = spec["function"]["parameters"]["properties"]
    assert "limit" in props, "a IA precisa poder escolher quantos trechos ver"
    assert props["limit"]["type"] == "integer"
    assert "query" in spec["function"]["parameters"]["required"]
    assert "limit" not in spec["function"]["parameters"]["required"]


# --------------------------------------------------------------------------- #
# Prompts lidos pelo MODELO devem estar em inglês (item 6)
# --------------------------------------------------------------------------- #
def _looks_portuguese(text: str) -> bool:
    marks = ("ção", "ções", "Resuma", "Descreva", "Trechos", "conversa abaixo", "não ")
    return any(m in text for m in marks)


def test_model_facing_prompts_are_english():
    from aiworkspace.chat.compaction_routes import _COMPACT_INSTRUCTION

    for name, text in (
        ("_VISION_DESCRIBE_PROMPT", orch._VISION_DESCRIBE_PROMPT),
        ("_COMPACT_INSTRUCTION", _COMPACT_INSTRUCTION),
        ("_NATIVE_TOOLS_NOTE", orch._NATIVE_TOOLS_NOTE),
        ("DEFAULT_TOOL_PROMPT", orch.DEFAULT_TOOL_PROMPT),
        ("TOOL_ACTION_GUARD", orch.TOOL_ACTION_GUARD),
        ("DEFAULT_TITLE_PROMPT", titles.DEFAULT_TITLE_PROMPT),
    ):
        assert not _looks_portuguese(text), f"{name} está em PT (o modelo lê em EN)"


def test_translated_prompts_keep_user_language_for_output():
    """Traduzir a INSTRUÇÃO não pode trocar o idioma da SAÍDA lida pelo usuário."""
    from aiworkspace.chat.compaction_routes import _COMPACT_INSTRUCTION

    assert "same language as the conversation" in _COMPACT_INSTRUCTION
    assert "same language as the conversation" in orch._VISION_DESCRIBE_PROMPT
