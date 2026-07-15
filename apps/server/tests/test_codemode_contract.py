"""Contrato do Modo Código (SIFT >= 0.8) + limpeza dos payloads do Gmail.

Na 0.7 nós remendávamos duas coisas no app: promover a última expressão a `output`
(senão a chamada voltava vazia) e avisar o modelo das regras do sandbox (senão ele
descobria os imports proibidos ERRANDO — cada erro custa uma volta inteira do turno).
A 0.8 resolveu ambas na lib e os remendos foram removidos. Estes testes GUARDAM esse
comportamento: se um upgrade da SIFT regredir, o desperdício de tokens volta calado."""
from __future__ import annotations

import json

from sift.codemode import CODE_SYSTEM_PROMPT
from sift.sandbox import SANDBOX_RULES, execute

from aiworkspace.integrations.google_service import _clean_text


def _run(code: str) -> dict:
    """Roda um snippet no sandbox da SIFT com helpers falsos (sem tools reais)."""
    return json.loads(execute(code, lambda p, **kw: {"ok": p}, lambda q: ["a.b.c"], lambda p: p))


# ------------------------- contrato do sandbox (SIFT) -------------------------

def test_trailing_expression_is_promoted_to_output():
    # o caso exato que voltava {"stdout": ""} na 0.7 e queimava a volta do turno
    assert _run('paths = search("gmail")\npaths') == {"output": ["a.b.c"]}


def test_explicit_output_still_wins():
    assert _run('x = 1\noutput = 42\nx')["output"] == 42


def test_nothing_assigned_returns_an_actionable_error():
    # falhar em SILÊNCIO é o pior caso: o modelo não tem como se corrigir
    r = _run("x = 1")
    assert "error" in r and "output" in r["error"]
    assert r.get("hint")


def test_import_error_carries_the_rules_as_hint():
    r = _run("import datetime")
    assert "SandboxError" in r["error"]
    assert r["hint"] == SANDBOX_RULES


def test_sandbox_rules_reach_the_model_prompt():
    # a nota que nós injetávamos à mão agora nasce da própria policy do sandbox
    assert SANDBOX_RULES in CODE_SYSTEM_PROMPT
    assert "no imports" in SANDBOX_RULES.lower()


# --------------------------- limpeza do Gmail (nosso) -------------------------

def test_clean_text_strips_invisible_padding():
    # o LinkedIn enche o snippet de U+034F p/ empurrar o preview — cada um é token pago
    dirty = "Veja quem viu seu perfil" + ("͏ " * 50) + "fim"
    clean = _clean_text(dirty)
    assert "͏" not in clean
    assert len(clean) < len(dirty) / 2
    assert clean.startswith("Veja quem viu seu perfil") and clean.endswith("fim")


def test_clean_text_collapses_rules_and_blank_lines():
    clean = _clean_text("Ola\n\n\n\n----------------\n\n\nTchau")
    assert "----" not in clean and "\n\n\n" not in clean
    assert "Ola" in clean and "Tchau" in clean


def test_clean_text_preserves_real_content():
    s = "Reuniao as 14h - sala 3 (traga o relatorio)"
    assert _clean_text(s) == s
