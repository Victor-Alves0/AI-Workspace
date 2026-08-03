"""Regressão: o contexto do turno (contextvars de toolctx) DEVE chegar dentro da tool.

O dispatch de builtin roda a tool SÍNCRONA numa thread do pool com teto de parede
(`_ToolDispatcher._dispatch_tp` → `run_in_executor`). `run_in_executor(None, fn)` NÃO
herda o `contextvars.Context` do turno (ao contrário de `run_in_threadpool`), então sem
copiar o contexto explicitamente a tool leria os DEFAULTS de toolctx — e um chat de
projeto legítimo veria `current_codespace_project_id=None` → code.graph/files/exec
respondendo "Nenhum projeto do Codespace vinculado". Este teste trava esse contrato.
"""
from __future__ import annotations

from aiworkspace.chat.orchestrator import _ToolDispatcher
from aiworkspace.tools import toolctx


class _FakeSift:
    """Simula sift.dispatch: lê os contextvars do turno de DENTRO da thread do pool."""
    def dispatch(self, name, args):
        return {
            "project_id": toolctx.current_codespace_project_id.get(),
            "chat_id": toolctx.current_chat_id.get(),
            "tz": toolctx.user_tz.get(),
            "bg": toolctx.background.get(),
        }


def _disp() -> _ToolDispatcher:
    d = _ToolDispatcher.__new__(_ToolDispatcher)  # só _dispatch_tp; só usa self.sift
    d.sift = _FakeSift()
    return d


async def test_contextvars_reach_the_tool_thread():
    toolctx.current_codespace_project_id.set("proj-abc")
    toolctx.current_chat_id.set("chat-xyz")
    toolctx.user_tz.set("America/Sao_Paulo")
    toolctx.background.set(True)
    try:
        got = await _disp()._dispatch_tp("code.graph.query", {})
    finally:
        toolctx.current_codespace_project_id.set(None)
        toolctx.current_chat_id.set(None)
        toolctx.user_tz.set("")
        toolctx.background.set(False)
    assert got["project_id"] == "proj-abc", "projeto do Codespace perdido na thread da tool"
    assert got["chat_id"] == "chat-xyz"
    assert got["tz"] == "America/Sao_Paulo"
    assert got["bg"] is True


async def test_tool_thread_sees_defaults_when_unset():
    """Sanidade: sem contexto setado, a tool vê os defaults (não vaza de outro turno)."""
    got = await _disp()._dispatch_tp("code.graph.query", {})
    assert got["project_id"] is None and got["chat_id"] is None
    assert got["tz"] == "" and got["bg"] is False
