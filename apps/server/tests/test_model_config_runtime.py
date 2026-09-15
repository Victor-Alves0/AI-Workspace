"""Regressões de ModelConfig: UUID é a identidade, não snapshots/nome."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aiworkspace.chat.turn_setup import (
    CHAT_REASONING_EFFORT_PARAM,
    _effective_chat_model,
    _params_with_chat_reasoning,
)
from aiworkspace.models_routes import ModelIn, ModelUpdate, _clean_slug, _ensure_slug_available


@pytest.mark.parametrize("field,limit", [("base_model", 255), ("name", 255), ("tts_voice", 64)])
def test_model_strings_cannot_exceed_database_column_limits(field, limit):
    with pytest.raises(ValidationError):
        ModelIn.model_validate({"name": "Test", "base_model": "test/model", field: "x" * (limit + 1)})
    with pytest.raises(ValidationError):
        ModelUpdate.model_validate({field: "x" * (limit + 1)})


@pytest.mark.parametrize("field", ["name", "base_model", "params", "filter_config", "tool_ids", "enabled"])
def test_partial_model_update_rejects_explicit_null_for_required_fields(field):
    with pytest.raises(ValidationError):
        ModelUpdate.model_validate({field: None})
    assert ModelUpdate().model_dump(exclude_unset=True) == {}
    assert ModelUpdate(tts_voice=None).model_dump(exclude_unset=True) == {"tts_voice": None}


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


def test_linked_chat_applies_only_its_explicit_reasoning_override():
    chat = SimpleNamespace(
        model="provider/old",
        system_prompt="old",
        params={
            "temperature": 0.1,
            "reasoning": {"effort": "low"},
            CHAT_REASONING_EFFORT_PARAM: "high",
        },
    )
    current = SimpleNamespace(
        base_model="provider/current",
        system_prompt="current",
        params={"temperature": 0.9, "reasoning": {"effort": "medium"}},
    )

    _, _, params = _effective_chat_model(chat, current)

    assert params == {"temperature": 0.9, "reasoning": {"effort": "high"}}
    assert CHAT_REASONING_EFFORT_PARAM not in params


def test_chat_reasoning_off_removes_model_default_and_reserved_param():
    params = _params_with_chat_reasoning(
        {"temperature": 0.7, "reasoning": {"effort": "high"}},
        {CHAT_REASONING_EFFORT_PARAM: "off"},
    )

    assert params == {"temperature": 0.7}


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
