"""Política pura da auto-compactação: segurança de janela + orçamento de latência."""
from __future__ import annotations

from aiworkspace.chat.compaction_service import _autocompact_reason


def test_latency_budget_compacts_large_window_before_physical_threshold():
    """Uma janela de 1M não deve aguardar 750k tokens para ficar responsiva."""
    assert _autocompact_reason(
        64_000, window=1_000_000, threshold=0.75, latency_budget_tokens=64_000,
    ) == "latency_budget"


def test_window_limit_remains_a_safety_backstop_when_budget_disabled():
    assert _autocompact_reason(
        75_000, window=100_000, threshold=0.75, latency_budget_tokens=0,
    ) == "window"
    assert _autocompact_reason(
        74_999, window=100_000, threshold=0.75, latency_budget_tokens=0,
    ) is None


def test_latency_budget_never_delays_a_smaller_window_limit():
    """O menor limite vence: orçamento não pode empurrar o chat além da janela."""
    assert _autocompact_reason(
        75_000, window=100_000, threshold=0.75, latency_budget_tokens=200_000,
    ) == "window"
