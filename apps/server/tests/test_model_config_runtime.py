"""Regressões de ModelConfig: UUID é a identidade, não snapshots/nome."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aiworkspace.chat.turn_setup import _effective_chat_model
from aiworkspace.models_routes import _clean_slug, _ensure_slug_available


def test_linked_chat_uses_current_model_config_instead_of_its_snapshot():
    chat = SimpleNamespace(
        model="provider/model-antigo",
        system_prompt="prompt antigo",
        params={"temperature": 0.1},
    )
    current = SimpleNamespace(
        base_model="provider/model-novo",
        system_prompt="prompt atualizado",
        params={"temperature": 0.9, "max_tokens": 2048},
    )

    model, prompt, params = _effective_chat_model(chat, current)

    assert model == "provider/model-novo"
    assert prompt == "prompt atualizado"
    assert params == {"temperature": 0.9, "max_tokens": 2048}
    # o dict entregue ao turno não pode mutar o JSONB que acabou de ser carregado
    params["temperature"] = 0.2
    assert current.params["temperature"] == 0.9


def test_unlinked_chat_keeps_its_own_configuration():
    chat = SimpleNamespace(
        model="provider/model-do-chat",
        system_prompt="prompt do chat",
        params={"top_p": 0.8},
    )

    assert _effective_chat_model(chat, None) == (
        "provider/model-do-chat", "prompt do chat", {"top_p": 0.8}
    )


def test_slug_is_trimmed_and_empty_slug_is_not_an_identifier():
    assert _clean_slug("  meu-modelo  ") == "meu-modelo"
    assert _clean_slug("   ") is None
    assert _clean_slug(None) is None


class _DuplicateSlugDb:
    async def scalar(self, _statement):
        return "other-model-id"


@pytest.mark.asyncio
async def test_duplicate_model_slug_gets_a_clear_conflict():
    with pytest.raises(HTTPException) as exc:
        await _ensure_slug_available(
            _DuplicateSlugDb(), SimpleNamespace(id="user-id"), "assistente"
        )

    assert exc.value.status_code == 409
    assert "@assistente" in str(exc.value.detail)
