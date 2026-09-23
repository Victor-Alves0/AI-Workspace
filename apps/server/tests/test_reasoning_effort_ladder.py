"""Nível de raciocínio recusado pelo provider: desce um degrau e refaz.

A UI oferece Desligado/Baixo/Médio/Alto/Máximo ("Mínimo" saiu). Abaixo de Baixo o
raciocínio é desligado; "minimal" ainda chega pela API pública e de chats antigos.
"""
from __future__ import annotations

import pytest

from aiworkspace.providers.openrouter import _lower_effort

_ERRO = "unsupported value for reasoning effort"


@pytest.mark.parametrize("atual,proximo", [
    ("xhigh", "high"), ("high", "medium"), ("medium", "low"), ("low", "off"), ("minimal", "off"),
])
def test_desce_um_degrau_ate_desligar(atual, proximo):
    payload = {"reasoning": {"effort": atual}}

    assert _lower_effort(payload, _ERRO) == proximo
    if proximo == "off":
        assert "reasoning" not in payload
    else:
        assert payload["reasoning"]["effort"] == proximo


def test_formato_plano_da_api_openai():
    payload = {"reasoning_effort": "low"}

    assert _lower_effort(payload, _ERRO) == "off"
    assert "reasoning_effort" not in payload


def test_erro_que_nao_e_de_raciocinio_nao_mexe():
    payload = {"reasoning": {"effort": "high"}}

    assert _lower_effort(payload, "rate limit exceeded") is None
    assert payload == {"reasoning": {"effort": "high"}}
