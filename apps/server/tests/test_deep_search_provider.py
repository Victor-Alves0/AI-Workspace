"""Pesquisa profunda não depende mais do OpenRouter: os passos internos vão para o
provedor do modelo escolhido (Ollama, provedor próprio…)."""
from __future__ import annotations

import asyncio

from aiworkspace import deep_search


def test_internal_steps_use_the_configured_provider(monkeypatch):
    seen = []

    async def fake_complete(api_key, model, messages, *, params=None, timeout=0, base_url=None):
        seen.append((api_key, model, base_url))
        return '["q1"]' if "plan" in messages[0]["content"].lower() else "Resposta [1]\n## Key findings\n- x"

    async def fake_search(q):
        return [{"title": "t", "url": "https://ex.com/a", "snippet": "conteúdo"}]

    from aiworkspace.providers import openrouter
    monkeypatch.setattr(openrouter, "complete", fake_complete)
    cfg = deep_search.DeepSearchConfig(api_key="ollama", model="ollama/qwen", base_url="http://x/v1",
                                       read_pages=False, max_rounds=1, web_search=fake_search)
    out = asyncio.run(deep_search.run("tema", cfg))
    assert seen and all(s == ("ollama", "ollama/qwen", "http://x/v1") for s in seen)
    assert "error" not in out


def test_without_any_model_the_error_says_what_to_do():
    out = asyncio.run(deep_search.run("tema", deep_search.DeepSearchConfig()))
    assert "OpenRouter" in out["error"] and "model" in out["error"]
