"""Continuação automática: quando uma volta termina SÓ com raciocínio (ou vazia), o
loop cutuca o próprio modelo ("continue e responda agora") antes de cair na síntese em
prompt limpo — em vez de salvar o ⚠️ "O modelo não retornou uma resposta final".
Limitada (_CONTINUE_NUDGE_MAX) p/ não girar nem dobrar a conta. Inspirado no opencode
(MAX_STEPS_PROMPT ao esgotar os passos; "Continue if you have next steps…")."""
from __future__ import annotations

import asyncio

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn
from aiworkspace.chat.turn_setup import _final_message_fields

_SYNTH_MARK = "finalizando a resposta"
_NUDGE_MARK = "[harness] Your last turn ended with no reply"


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
        return '{"price": "BTC subiu 40%"}'


def _reasoning_only(text="Preciso montar o gráfico com os dados..."):
    return {"choices": [{"delta": {"reasoning": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}}


def _empty():
    return {"choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 0, "total_tokens": 50}}


def _text(content):
    return {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 9, "total_tokens": 59}}


def _tool_chunk():
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "web.search.query", "arguments": '{"q":"btc"}'}}]},
            "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105}}


def _run(script):
    """`script(call_idx, messages, tools)` devolve o chunk da chamada. Registra as chamadas."""
    calls: list[dict] = []
    orig = orch.openrouter.stream_chat

    def fake_stream(api_key, model, messages, *, tools=None, params=None,
                    modalities=None, base_url=None):
        synth = any(_SYNTH_MARK in (m.get("content") or "") for m in messages)
        calls.append({"messages": list(messages), "tools": tools, "synth": synth})

        async def gen():
            if synth:
                yield _text("Síntese limpa.")
            else:
                yield script(sum(1 for c in calls if not c["synth"]) - 1, messages, tools)
        return gen()

    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="gráfico do bitcoin",
                chat_system_prompt=None, params={}, use_tools=True, sift=FakeSift(),
                session=TurnSession(user_id="u"))]
        return asyncio.run(go()), calls
    finally:
        orch.openrouter.stream_chat = orig


def _done(events):
    return [e for e in events if e["type"] == "done"][0]


def _nudges(msgs):
    return [m for m in msgs if m.get("role") == "user" and _NUDGE_MARK in (m.get("content") or "")]


def test_so_raciocinio_ganha_cutucada_e_o_modelo_responde_de_verdade():
    events, calls = _run(lambda i, m, t: _reasoning_only() if i == 0 else _text("Aqui está o gráfico."))
    assert _done(events)["content"] == "Aqui está o gráfico."
    assert not any(c["synth"] for c in calls)  # nem precisou da síntese
    nud = _nudges(calls[1]["messages"])
    assert len(nud) == 1
    # o raciocínio da volta vazia volta junto: o modelo não recomeça do zero
    assert "Preciso montar o gráfico" in nud[0]["content"]
    assert calls[1]["tools"] is not None  # ainda longe do teto: pode chamar mais uma tool


def test_resposta_totalmente_vazia_tambem_e_cutucada():
    events, calls = _run(lambda i, m, t: _empty() if i == 0 else _text("Pronto."))
    assert _done(events)["content"] == "Pronto."
    assert len(_nudges(calls[1]["messages"])) == 1


def test_cutucada_depois_de_tool_mantem_as_tools_e_o_resultado():
    seq = [_tool_chunk(), _reasoning_only(), _text("BTC subiu 40%.")]
    events, calls = _run(lambda i, m, t: seq[i])
    assert _done(events)["content"] == "BTC subiu 40%."
    assert calls[2]["tools"] is not None
    assert any(m.get("role") == "tool" for m in calls[2]["messages"])


def test_cutucadas_sao_limitadas_e_depois_cai_na_sintese():
    """Modelo que só pensa: no máximo _CONTINUE_NUDGE_MAX re-chamadas, depois síntese."""
    events, calls = _run(lambda i, m, t: _reasoning_only())
    normais = [c for c in calls if not c["synth"]]
    assert len(normais) == 1 + orch._CONTINUE_NUDGE_MAX
    assert sum(1 for c in calls if c["synth"]) == 1
    assert _done(events)["content"] == "Síntese limpa."
    assert len(_nudges(normais[-1]["messages"])) == orch._CONTINUE_NUDGE_MAX


def test_teto_de_iteracoes_avisa_uma_vez_que_as_tools_acabaram(monkeypatch):
    """Como o MAX_STEPS_PROMPT do opencode: cortar as tools COM aviso, não em silêncio."""
    monkeypatch.setattr(orch.get_settings(), "max_tool_iterations", 3)
    events, calls = _run(lambda i, m, t: _tool_chunk() if t is not None else _text("Resumo final."))
    assert _done(events)["content"] == "Resumo final."
    ultima = [c for c in calls if not c["synth"]][-1]
    assert ultima["tools"] is None
    avisos = [m for m in ultima["messages"] if m.get("content") == orch._MAX_STEPS_NOTE]
    assert len(avisos) == 1


def test_texto_normal_nao_gera_cutucada():
    events, calls = _run(lambda i, m, t: _text("Oi!"))
    assert _done(events)["content"] == "Oi!"
    assert len(calls) == 1


# ------------------------------ persistência -------------------------------- #
def test_parada_do_usuario_nao_culpa_o_modelo():
    content, _ = _final_message_fields({"content": "", "streamed": "", "reasoning": {"text": "x"},
                                        "stopped": True})
    assert "interrompida" in content and "não retornou" not in content


def test_sem_texto_e_sem_parada_segue_o_aviso_de_ultimo_recurso():
    content, _ = _final_message_fields({"content": "", "streamed": "", "reasoning": {"text": "x"}})
    assert "não retornou uma resposta final" in content
