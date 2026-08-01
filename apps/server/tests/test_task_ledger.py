"""Ledger de tarefa: rendering + ciclo de vida do achado (puro/hermético).

O `apply()` toca o banco (engine efêmera) — coberto no smoke E2E. Aqui foco no que é
pura lógica de harness e não pode regredir: o bloco injetado mostra o plano com status,
mostra o achado REFUTADO com a marca ✗ + a REGRA de não reportá-lo, e some quando não há
objetivo/plano (não polui o contexto à toa).
"""
from __future__ import annotations

from aiworkspace.chat import ledger_service as ls


def test_render_empty_is_blank():
    assert ls.render_block(None) == ""
    assert ls.render_block({"objective": "", "plan": [], "findings": [], "next_step": ""}) == ""


def test_render_shows_plan_status_and_next():
    led = {
        "objective": "Refatorar o módulo de auth", "status": "active",
        "plan": [
            {"id": "s1", "text": "mapear chamadas", "status": "done", "note": ""},
            {"id": "s2", "text": "extrair helper", "status": "doing", "note": "em b.py"},
            {"id": "s3", "text": "rodar testes", "status": "todo", "note": ""},
        ],
        "findings": [], "notes": [], "next_step": "terminar s2",
    }
    out = ls.render_block(led)
    assert "Refatorar o módulo de auth" in out
    assert "[x] [s1]" in out          # done
    assert "[»] [s2]" in out          # doing
    assert "[ ] [s3]" in out          # todo
    assert "Próximo passo: terminar s2" in out


def test_refuted_finding_is_marked_and_ruled_out():
    led = {
        "objective": "Avaliar exposição anônima", "status": "active", "plan": [],
        "findings": [
            {"id": "f1", "text": "SSRF em HTTP Actions", "status": "refuted",
             "evidence": "POST /api/action type=http -> 400 not supported"},
            {"id": "f2", "text": "SMTP mutável por não-admin", "status": "confirmed",
             "evidence": "403 para user comum"},
            {"id": "f3", "text": "SCIM sem chave", "status": "open", "evidence": ""},
        ],
        "notes": [], "next_step": "",
    }
    out = ls.render_block(led)
    assert "✗ [f1]" in out            # refutado marcado
    assert "✓ [f2]" in out            # confirmado
    assert "? [f3]" in out            # aberto
    # a REGRA anti-ancoragem está no bloco (não reportar refutado como válido)
    assert "refuted" in out.lower() and "não o reporte" in out.lower()


def test_status_vocabularies():
    # contrato que a tool valida — se mudar, a tool e o render têm que acompanhar
    assert ls._PLAN_STATUSES == ("todo", "doing", "done", "blocked")
    assert ls._FIND_STATUSES == ("open", "confirmed", "refuted")
