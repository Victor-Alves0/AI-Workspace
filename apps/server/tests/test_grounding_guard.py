"""Guarda-juiz de fundamentacao/objetivo (include_ledger).

Gap #1 da analise do Metabase: o modelo derivou do objetivo (validou correcoes quando o
objetivo era atacar). O guarda-juiz passa a enxergar o Ledger e julgar deriva/ausencia de
fundamento. Aqui cobrimos: o juiz recebe o ledger no prompt e o preenchimento do criterio
padrao quando include_ledger vem sem criterio proprio.
"""
from __future__ import annotations

import asyncio

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat import turn_setup as ts


def test_judge_includes_ledger_and_triggers_on_drift(monkeypatch=None):
    seen = {}

    async def fake_complete(key, model, messages, base_url=None, timeout=30.0):
        seen["prompt"] = messages[-1]["content"]
        p = seen["prompt"].lower()
        # "juiz": aciona so quando a RESPOSTA tem a frase de deriva (o proprio criterio
        # menciona 'corrigir', entao usamos uma frase que so existe na resposta de drift).
        return "SIM" if "corrigi as falhas" in p else "NAO"

    orig = orch.openrouter.complete
    orch.openrouter.complete = fake_complete
    try:
        guard = {"judge_model": "x/y", "_judge_api_key": "k",
                 "criterion": ts._GROUNDING_CRITERION, "include_ledger": True}
        ledger = "Objetivo [active]: Atacar o alvo autorizado, sem corrigir nada."
        drift = "Corrigi as falhas e rodei os testes."
        aligned = "Requisicoes anonimas: /api/setting 401. Sem bypass."
        assert asyncio.run(orch._judge_triggered(guard, drift, "continue", ledger)) is True
        assert "Ledger" in seen["prompt"] or "Objetivo" in seen["prompt"]
        assert asyncio.run(orch._judge_triggered(guard, aligned, "continue", ledger)) is False
        # sem ledger_block -> juiz nao recebe contexto do ledger
        asyncio.run(orch._judge_triggered(guard, drift, "continue", ""))
        assert "Objetivo [active]" not in seen["prompt"]
    finally:
        orch.openrouter.complete = orig


def test_default_criterion_fills_when_include_ledger_without_criterion():
    guard = {"include_ledger": True, "criterion": "", "action": "reinforce", "inject_text": ""}
    if guard["include_ledger"] and not guard["criterion"].strip():
        guard["criterion"] = ts._GROUNDING_CRITERION
    assert guard["criterion"].strip()
    if guard["action"] == "reinforce" and not guard["inject_text"].strip():
        guard["inject_text"] = ts._GROUNDING_REINFORCE if guard["include_ledger"] else ts._DEFAULT_REINFORCE
    assert "reancore" in guard["inject_text"].lower()
