"""Tamanho do contexto × entrada somada.

`prompt_tokens` é a SOMA de todas as chamadas do turno (cada passo de ferramenta
reenvia o contexto inteiro); o tamanho real é o `context_tokens` (prompt da 1ª chamada).
Ele era calculado mas não ia para a mensagem gravada: o medidor, depois de recarregar,
caía numa estimativa e a auto-compactação lia a soma — N× maior que o contexto.
"""
from __future__ import annotations

from aiworkspace.chat.orchestrator import _merge_usage
from aiworkspace.chat.turn_setup import _usage_record


def test_cada_chamada_ao_modelo_e_contada():
    total: dict = {}
    for prompt in (20_000, 21_000, 22_500):          # 1 resposta + 2 passos de ferramenta
        _merge_usage(total, {"prompt_tokens": prompt, "completion_tokens": 100})
    _merge_usage(total, {"cost": 0.01})              # custo de subagente: não é chamada

    assert total["llm_calls"] == 3
    assert total["prompt_tokens"] == 63_500


def test_registro_da_mensagem_guarda_contexto_e_chamadas():
    rec = _usage_record(
        {"prompt_tokens": 63_500, "completion_tokens": 300, "context_tokens": 20_000,
         "llm_calls": 3, "cached_tokens": 40_000},
        "openai/gpt-4o-mini", None,
    )

    assert rec["context_tokens"] == 20_000
    assert rec["llm_calls"] == 3
    assert rec["prompt_tokens"] == 63_500 and rec["cached_tokens"] == 40_000


def test_turno_sem_contexto_medido_nao_inventa_o_campo():
    rec = _usage_record({"prompt_tokens": 10}, "m", None)

    assert "context_tokens" not in rec and rec["llm_calls"] == 0
