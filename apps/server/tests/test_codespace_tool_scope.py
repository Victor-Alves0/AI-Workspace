"""Codespace: exposição das tools de código num chat vinculado a um projeto.

Num chat de projeto as tools de LEITURA de código entram no escopo mesmo sem
estarem marcadas no modelo (vincular o chat ao projeto é o consentimento), e as
de código que estão no escopo são FIXADAS (specs de 1ª classe, sem discovery).
A de ESCRITA nunca é auto-liberada — continua opt-in por-modelo.
"""
from __future__ import annotations

from aiworkspace.tools.loader import codespace_allow, codespace_pins


# --------------------------------- allow ----------------------------------- #
def test_read_tools_are_auto_allowed():
    """Modelo sem NENHUMA tool de código marcada ainda navega o projeto."""
    allow = codespace_allow(["utils.time.now"])
    assert "code.graph.query" in allow
    assert "code.files.browse" in allow
    assert "code.flow.analyze" in allow  # análise de fluxo também é leitura
    assert "utils.time.now" in allow  # não perde o que o modelo já tinha


def test_write_tool_is_never_auto_allowed():
    """Escrita cria/apaga arquivos e faz push: exige permissão explícita."""
    assert "code.files.write" not in codespace_allow(["utils.time.now"])
    assert "code.files.write" not in codespace_allow([])


def test_allow_is_idempotent_when_model_already_has_them():
    allow = codespace_allow(["code.graph.query", "code.files.browse", "code.files.write"])
    assert allow.count("code.graph.query") == 1
    assert "code.files.write" in allow  # marcada no modelo => permanece


# ---------------------------------- pins ----------------------------------- #
def test_code_tools_in_scope_get_pinned():
    allow = codespace_allow([])
    pins = codespace_pins([], allow)
    assert set(pins) == {"code.graph.query", "code.files.browse", "code.flow.analyze"}


def test_write_is_pinned_only_when_model_equipped_it():
    # sem a de escrita no escopo: não é fixada (nem existe pro modelo)
    assert "code.files.write" not in codespace_pins([], codespace_allow([]))
    # com ela equipada: entra no escopo E é fixada
    allow = codespace_allow(["code.files.write"])
    assert "code.files.write" in codespace_pins([], allow)


def test_existing_pins_are_preserved():
    """Os pins escolhidos à mão no modelo não são substituídos pelos do Codespace."""
    pins = codespace_pins(["web.search.query"], codespace_allow([]))
    assert "web.search.query" in pins
    assert "code.graph.query" in pins


def test_pins_do_not_duplicate_when_already_pinned():
    allow = codespace_allow([])
    pins = codespace_pins(["code.graph.query"], allow)
    assert pins.count("code.graph.query") == 1


def test_wildcard_allow_pattern_counts_as_in_scope():
    """Seleções antigas de 2 segmentos viram 'code.files.*' — devem ser fixadas."""
    pins = codespace_pins([], ["code.files.*"])
    assert "code.files.browse" in pins
    assert "code.files.write" in pins  # o wildcard do modelo JÁ dava permissão
