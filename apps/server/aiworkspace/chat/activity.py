"""Ordered, display-only activity history; never added to the model context."""

from functools import wraps


class ActivityTrace:
    def __init__(self):
        self.steps: list[dict] = []
        self.pending = ""

    def archive(self):
        if self.pending.strip():
            self.steps.append({"kind": "commentary", "text": self.pending})
        self.pending = ""

    def add(self, event: dict):
        kind = event.get("type")
        if kind == "token":
            self.pending += event.get("text", "")
        elif kind == "reasoning":
            if self.pending:
                self.archive()
            text = event.get("text", "")
            if self.steps and self.steps[-1]["kind"] == "reasoning":
                self.steps[-1]["text"] += text
            elif text:
                self.steps.append({"kind": "reasoning", "text": text})
        elif kind in {"tool_call", "tool_result"}:
            if kind == "tool_call":
                self.archive()
            self.steps.append({"kind": "tool", "event": {
                "kind": "call" if kind == "tool_call" else "result",
                "name": event.get("name"),
                "data": event.get("arguments" if kind == "tool_call" else "result"),
            }})
        elif kind == "guard_reset":
            self.archive()
            self.steps.append({"kind": "commentary", "text": "Revisando a resposta após a verificação de saída."})

    def reasoning(self, original=None):
        if not self.steps:
            return original
        return {"text": "", **(original or {}), "steps": [dict(step) for step in self.steps]}


def with_activity(function):
    """Enrich terminal snapshots for every consumer, including guarded retries."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        trace = ActivityTrace()
        async for event in function(*args, **kwargs):
            trace.add(event)
            if event.get("type") == "done":
                event = {**event, "reasoning": trace.reasoning(event.get("reasoning"))}
            yield event
    return wrapped
