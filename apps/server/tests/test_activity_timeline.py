"""Regression: intermediate narration survives tools, retries and interruption."""

import pytest

from aiworkspace.chat import generation
from aiworkspace.chat.activity import ActivityTrace, with_activity


@pytest.mark.asyncio
async def test_multiple_tool_rounds_preserve_order_and_keep_final_separate():
    @with_activity
    async def source():
        yield {"type": "reasoning", "text": "Checking options"}
        yield {"type": "token", "text": "Vou verificar X."}
        yield {"type": "tool_call", "name": "X", "arguments": {}}
        yield {"type": "tool_result", "name": "X", "result": "unsupported"}
        yield {"type": "token", "text": "X não faz isso. "}
        yield {"type": "token", "text": "Vou verificar Y."}
        yield {"type": "tool_call", "name": "Y", "arguments": {}}
        yield {"type": "tool_result", "name": "Y", "result": "supported"}
        yield {"type": "token", "text": "Use Y."}
        yield {"type": "done", "content": "Use Y.", "reasoning": {"text": "Checking options", "seconds": 2}}

    events = [event async for event in source()]
    final = events[-1]
    assert final["content"] == "Use Y."
    assert final["reasoning"]["seconds"] == 2
    steps = final["reasoning"]["steps"]
    assert [step["kind"] for step in steps] == ["reasoning", "commentary", "tool", "tool", "commentary", "tool", "tool"]
    assert steps[1]["text"] == "Vou verificar X."
    assert steps[4]["text"] == "X não faz isso. Vou verificar Y."
    assert all(step.get("text") != "Use Y." for step in steps)


def test_partial_and_guard_retry_keep_previous_steps():
    trace = ActivityTrace()
    trace.add({"type": "token", "text": "Checking X"})
    trace.add({"type": "tool_call", "name": "X", "arguments": {}})
    assert trace.pending == ""
    partial = trace.reasoning()
    assert partial["steps"][0]["text"] == "Checking X"
    trace.add({"type": "token", "text": "First draft"})
    trace.add({"type": "guard_reset"})
    trace.add({"type": "reasoning", "text": "New attempt"})
    assert len(partial["steps"]) == 2
    assert trace.reasoning()["steps"][2]["text"] == "First draft"


@pytest.mark.asyncio
async def test_plain_answer_does_not_create_empty_thinking_section():
    @with_activity
    async def source():
        yield {"type": "token", "text": "Hello"}
        yield {"type": "done", "content": "Hello", "reasoning": None}

    events = [event async for event in source()]
    assert events[-1]["reasoning"] is None


@pytest.mark.asyncio
async def test_driver_preserves_activity_when_source_fails_before_done():
    saved = {}

    async def source():
        yield {"type": "token", "text": "Vou verificar X."}
        yield {"type": "tool_call", "name": "X", "arguments": {}}
        raise RuntimeError("connection lost")

    async def save(collected, emit):
        saved.update(collected)

    gen = generation.start("activity-partial-test", source(), on_finish=save)
    await gen.task
    assert saved["streamed"] == ""
    assert saved["reasoning"]["steps"][0]["text"] == "Vou verificar X."
    assert saved["reasoning"]["steps"][1]["event"]["name"] == "X"
    assert saved["error"] == "connection lost"
