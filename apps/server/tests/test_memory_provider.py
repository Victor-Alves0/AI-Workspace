"""A extração de memória usa o provedor DA CONVERSA.

Antes: sempre OpenRouter + gpt-4o-mini, com a chave da conversa. Quem conversa pelo
Ollama ou pela assinatura do ChatGPT tomava 401 e a memória nunca era gravada — só um
aviso no log."""
from __future__ import annotations

from aiworkspace.chat import orchestrator as orch
from aiworkspace.memory import memory_service


def test_memory_model_follows_the_conversation_provider():
    assert orch._memory_llm("ollama/llama3", "http://127.0.0.1:11434/v1", "") == \
        ("ollama/llama3", "http://127.0.0.1:11434/v1")
    assert orch._memory_llm("codex/gpt-5", None, "")[0] == "codex/gpt-5"
    # OpenRouter: o auxiliar quando é um id do OpenRouter; senão o barato de sempre
    assert orch._memory_llm("anthropic/claude-x", None, "google/gemini-flash") == ("google/gemini-flash", None)
    assert orch._memory_llm("anthropic/claude-x", None, "ollama/qwen") == (memory_service._LLM_MODEL, None)
    assert orch._memory_llm("anthropic/claude-x", None, "") == (memory_service._LLM_MODEL, None)


def test_llm_json_goes_through_the_provider_layer(monkeypatch):
    seen = {}

    async def fake_complete(api_key, model, messages, *, params=None, timeout=0, base_url=None):
        seen.update(key=api_key, model=model, base=base_url, params=params)
        return '```json\n{"facts": ["Gosta de café"]}\n```'

    from aiworkspace.providers import openrouter
    monkeypatch.setattr(openrouter, "complete", fake_complete)
    out = memory_service._llm_json("k-ollama", "sys", "user", ("ollama/llama3", "http://x/v1"))
    assert out == {"facts": ["Gosta de café"]}
    assert seen["model"] == "ollama/llama3" and seen["base"] == "http://x/v1"
    assert "response_format" not in seen["params"]  # nem todo provedor aceita
