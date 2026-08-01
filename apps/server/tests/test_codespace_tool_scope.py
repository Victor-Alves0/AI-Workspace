"""Codespace: exposição das tools de código num chat vinculado a um projeto.

Num chat de projeto TODAS as tools de código entram no escopo (mesmo sem estarem
marcadas no modelo) e são FIXADAS (specs de 1ª classe, sem discovery) — vincular o
chat ao projeto É o consentimento de trabalhar naquele código, como Codex/Claude
Code/opencode. A segurança fica nas camadas certas: `code.exec.run` ainda depende do
`exec_enabled` por-projeto na hora de rodar, e a escrita é local/reversível (push é
ação separada com confirmação).
"""
from __future__ import annotations

from aiworkspace.tools.loader import codespace_allow, codespace_pins

_ALL = {
    "code.graph.query", "code.files.browse", "code.flow.analyze",
    "code.files.write", "code.exec.run", "code.exec.jobs", "code.preview.serve",
    "code.task.manage",
}


# --------------------------------- allow ----------------------------------- #
def test_all_code_tools_are_auto_allowed():
    """Modelo sem NENHUMA tool de código marcada ainda navega, escreve e roda."""
    allow = codespace_allow(["utils.time.now"])
    assert _ALL <= set(allow)  # leitura + escrita + execução + tarefas
    assert "utils.time.now" in allow  # não perde o que o modelo já tinha


def test_allow_is_idempotent_when_model_already_has_them():
    allow = codespace_allow(["code.graph.query", "code.files.browse", "code.files.write"])
    assert allow.count("code.graph.query") == 1
    assert allow.count("code.files.write") == 1
    assert _ALL <= set(allow)


# ---------------------------------- pins ----------------------------------- #
def test_all_code_tools_in_scope_get_pinned():
    """Num chat de projeto, todas as tools de código viram specs de 1ª classe."""
    allow = codespace_allow([])
    pins = codespace_pins([], allow)
    assert set(pins) == _ALL


def test_exec_and_write_are_pinned_without_model_equipping_them():
    """O que muda vs. o comportamento antigo: escrita/execução NÃO exigem mais
    opt-in por-modelo — o vínculo com o projeto já as libera e fixa."""
    allow = codespace_allow([])
    pins = codespace_pins([], allow)
    assert "code.files.write" in pins
    assert "code.exec.run" in pins
    assert "code.task.manage" in pins


def test_existing_pins_are_preserved():
    """Os pins escolhidos à mão no modelo não são substituídos pelos do Codespace."""
    pins = codespace_pins(["web.search.query"], codespace_allow([]))
    assert "web.search.query" in pins
    assert _ALL <= set(pins)


def test_pins_do_not_duplicate_when_already_pinned():
    allow = codespace_allow([])
    pins = codespace_pins(["code.graph.query"], allow)
    assert pins.count("code.graph.query") == 1


def test_wildcard_allow_pattern_counts_as_in_scope():
    """Seleções antigas de 2 segmentos viram 'code.files.*' — devem ser fixadas."""
    pins = codespace_pins([], ["code.files.*"])
    assert "code.files.browse" in pins
    assert "code.files.write" in pins  # o wildcard do modelo JÁ dava permissão
