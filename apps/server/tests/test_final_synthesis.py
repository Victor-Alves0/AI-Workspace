"""Síntese final garantida: quando o modelo termina o turno SEM redigir (preso no
formato de tool-call), o harness NUNCA devolve "regenere/troque de modelo". Reredigimos
num prompt LIMPO com o mesmo modelo (camada A); se ainda vier vazio, um resumo
determinístico entrega a substância (camada C). Ver orchestrator._synthesis_messages."""
from __future__ import annotations

import asyncio

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn

_SYNTH_MARK = "finalizando a resposta"  # trecho do system prompt de síntese


class FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"
    meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "web.search.query",
                 "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        return '{"finding": "SCIM PATCH aceita privilege escalation"}'


def _tool_chunk():
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "web.search.query", "arguments": '{"q":"x"}'}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}


def _text(content: str):
    return {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 9, "total_tokens": 59}}


def _run(fake_stream, *, profile=None):
    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="ataque o Metabase",
                chat_system_prompt=None, params={}, use_tools=True, sift=FakeSift(),
                session=TurnSession(user_id="u", user_profile=profile))]
        return asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig


def _is_synth(messages) -> bool:
    return any(_SYNTH_MARK in (m.get("content") or "") for m in messages)


def test_camada_a_prompt_limpo_destrava():
    """Modelo preso no formato de tool-call, MAS um prompt limpo (a síntese) o solta."""
    def fake_stream(api_key, model, messages, *, tools=None, params=None,
                    modalities=None, base_url=None):
        async def gen():
            if _is_synth(messages):  # prompt de síntese → redige de verdade
                yield _text("Confirmei o SCIM PATCH: escalonamento de privilégio real.")
            else:  # no loop normal, só vaza chamadas (nunca texto)
                yield _tool_chunk()
        return gen()

    events = _run(fake_stream)
    done = [e for e in events if e["type"] == "done"][0]
    assert "SCIM PATCH" in done["content"], done["content"][:120]
    assert "Regenerar" not in done["content"]
    # a síntese streamou tokens ao vivo (o usuário vê a resposta materializar)
    assert any(e["type"] == "token" and "SCIM" in e.get("text", "") for e in events)


def test_camada_c_digest_deterministico():
    """Modelo NUNCA redige (nem no prompt limpo) e não há modelo auxiliar → digest."""
    def fake_stream(api_key, model, messages, *, tools=None, params=None,
                    modalities=None, base_url=None):
        async def gen():
            yield _tool_chunk()  # sempre tool-call, jamais texto
        return gen()

    events = _run(fake_stream)
    done = [e for e in events if e["type"] == "done"][0]
    # nunca o erro frágil antigo; sempre a substância apurada
    assert "Regenerar" not in done["content"]
    assert "web.search.query" in done["content"], done["content"][:200]
    assert "SCIM PATCH" in done["content"], done["content"][:200]
