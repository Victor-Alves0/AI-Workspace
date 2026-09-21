"""Raciocínio que volta ao provedor junto da chamada de ferramenta.

O modelo pensa, chama uma ferramenta, recebe o resultado e continua. Reenviávamos a
chamada SEM o pensamento: o Claude com pensamento estendido e o Gemini 3 exigem os
blocos assinados de volta (`reasoning_details`) para seguir depois do resultado, e os
demais modelos retomavam sem lembrar por que tinham chamado a ferramenta.

As regras são as do provedor oficial do OpenRouter para o AI SDK (o mesmo que o
opencode usa): fundir texto consecutivo, reenviar só o que tem assinatura quando o
formato exige, e descartar raciocínio que chega depois que a resposta começou.
"""
from __future__ import annotations

import asyncio

from aiworkspace.chat import orchestrator as orch
from aiworkspace.chat.orchestrator import TurnSession, run_turn
from aiworkspace.providers import reasoning_details as rd


# --------------------------------------------------------------------------- #
# O módulo                                                                     #
# --------------------------------------------------------------------------- #
def test_texto_consecutivo_vira_um_bloco_com_a_assinatura_do_fim():
    """A assinatura chega no ÚLTIMO delta; o bloco reenviado precisa tê-la."""
    acc: list[dict] = []
    rd.accumulate(acc, [{"type": "reasoning.text", "text": "Preciso ", "format": "anthropic-claude-v1"}])
    rd.accumulate(acc, [{"type": "reasoning.text", "text": "buscar o clima."}])
    rd.accumulate(acc, [{"type": "reasoning.text", "text": "", "signature": "sig-123"}])

    assert acc == [{"type": "reasoning.text", "text": "Preciso buscar o clima.",
                    "format": "anthropic-claude-v1", "signature": "sig-123"}]


def test_bloco_cifrado_nao_se_funde_nem_tem_texto():
    acc: list[dict] = []
    rd.accumulate(acc, [{"type": "reasoning.text", "text": "a"}])
    rd.accumulate(acc, [{"type": "reasoning.encrypted", "data": "OPACO"}])
    rd.accumulate(acc, [{"type": "reasoning.text", "text": "b"}])

    assert [d["type"] for d in acc] == ["reasoning.text", "reasoning.encrypted", "reasoning.text"]
    assert rd.text_of([{"type": "reasoning.encrypted", "data": "OPACO"}]) == ""
    assert rd.text_of([{"type": "reasoning.summary", "summary": "resumo"}]) == "resumo"


def test_so_volta_o_que_o_provedor_aceita():
    """Texto de Claude/Gemini sem assinatura derruba o pedido inteiro. Sem `format`,
    o padrão é Claude. Outros formatos (DeepSeek, OpenAI…) não exigem assinatura."""
    blocos = [
        {"type": "reasoning.text", "text": "claude assinado", "format": "anthropic-claude-v1", "signature": "s"},
        {"type": "reasoning.text", "text": "claude sem assinatura", "format": "anthropic-claude-v1"},
        {"type": "reasoning.text", "text": "sem formato nem assinatura"},
        {"type": "reasoning.text", "text": "gemini sem assinatura", "format": "google-gemini-v1"},
        {"type": "reasoning.text", "text": "deepseek", "format": "unknown"},
        {"type": "reasoning.encrypted", "data": "OPACO", "format": "google-gemini-v1"},
    ]

    textos = [b.get("text") or b.get("data") for b in rd.replayable(blocos)]

    assert textos == ["claude assinado", "deepseek", "OPACO"]


# --------------------------------------------------------------------------- #
# O turno inteiro                                                              #
# --------------------------------------------------------------------------- #
class _FakeSift:
    system_prompt = "SYS"
    code_system_prompt = "CODE-SYS"
    meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": ""}

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "weather.current.get",
                 "parameters": {"type": "object", "properties": {}}}}]

    def code_tools(self):
        return self.openai_tools()

    def dispatch(self, name, args):
        return '{"temp": 21}'


def _run(primeira_volta: list[dict]):
    chamadas: list[list[dict]] = []

    def fake_stream(api_key, model, messages, *, tools=None, params=None,
                    modalities=None, base_url=None):
        chamadas.append([dict(m) for m in messages])

        async def gen():
            if len(chamadas) == 1:
                for chunk in primeira_volta:
                    yield chunk
            else:
                yield {"choices": [{"delta": {"content": "Faz 21 °C."}, "finish_reason": "stop"}]}
        return gen()

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="anthropic/claude", history=[], user_text="como está o tempo?",
                chat_system_prompt=None, params={}, use_tools=True, sift=_FakeSift(),
                session=TurnSession(user_id="u"))]
        eventos = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig
    return eventos, chamadas


def _tool_call_chunk():
    return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
            "function": {"name": "weather.current.get", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}


def test_raciocinio_volta_junto_da_chamada_de_ferramenta():
    eventos, chamadas = _run([
        {"choices": [{"delta": {
            "reasoning": "Preciso do clima atual.",
            "reasoning_details": [{"type": "reasoning.text", "text": "Preciso do clima atual.",
                                   "format": "anthropic-claude-v1"}]}}]},
        {"choices": [{"delta": {
            "reasoning_details": [{"type": "reasoning.text", "text": "", "signature": "sig-9"}]}}]},
        _tool_call_chunk(),
    ])

    assistente = next(m for m in chamadas[1] if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistente["reasoning_details"] == [{
        "type": "reasoning.text", "text": "Preciso do clima atual.",
        "format": "anthropic-claude-v1", "signature": "sig-9",
    }]
    assert assistente["reasoning"] == "Preciso do clima atual."
    done = next(e for e in eventos if e["type"] == "done")
    assert done["content"] == "Faz 21 °C."


def test_sem_blocos_a_chamada_vai_como_antes():
    """Ollama e provedores sem `reasoning_details` não recebem campos novos."""
    _, chamadas = _run([
        {"choices": [{"delta": {"reasoning": "Preciso do clima."}}]},
        _tool_call_chunk(),
    ])

    assistente = next(m for m in chamadas[1] if m.get("role") == "assistant" and m.get("tool_calls"))
    assert "reasoning_details" not in assistente and "reasoning" not in assistente


def test_so_blocos_sem_texto_corrido_ainda_aparece_no_pensou_por():
    eventos, _ = _run([
        {"choices": [{"delta": {"reasoning_details": [
            {"type": "reasoning.summary", "summary": "Vou consultar o clima."}]}}]},
        _tool_call_chunk(),
    ])

    assert "".join(e["text"] for e in eventos if e["type"] == "reasoning") == "Vou consultar o clima."


def test_raciocinio_depois_da_resposta_e_descartado():
    """Mesma regra do provedor oficial: começou a resposta, o pensamento tardio não
    se intercala no "Pensou por…" nem vai para os blocos reenviados."""
    chamadas: list = []

    def fake_stream(api_key, model, messages, *, tools=None, params=None,
                    modalities=None, base_url=None):
        chamadas.append(messages)

        async def gen():
            yield {"choices": [{"delta": {"reasoning": "Pensando."}}]}
            yield {"choices": [{"delta": {"content": "Resposta."}}]}
            yield {"choices": [{"delta": {"reasoning": " Pensamento tardio."},
                                "finish_reason": "stop"}]}
        return gen()

    orig = orch.openrouter.stream_chat
    orch.openrouter.stream_chat = fake_stream
    try:
        async def go():
            return [ev async for ev in run_turn(
                api_key="k", model="m", history=[], user_text="oi",
                chat_system_prompt=None, params={}, use_tools=False, sift=None,
                session=TurnSession(user_id="u"))]
        eventos = asyncio.run(go())
    finally:
        orch.openrouter.stream_chat = orig

    done = next(e for e in eventos if e["type"] == "done")
    assert done["content"] == "Resposta."
    assert done["reasoning"]["text"] == "Pensando."


# --------------------------------------------------------------------------- #
# Prompt "quando usar" congelado nos modelos salvos                            #
# --------------------------------------------------------------------------- #
def test_copia_gravada_do_padrao_segue_o_padrao_de_hoje(monkeypatch):
    """Os modelos salvos tinham a CÓPIA do padrão gravada (o editor salvava o texto do
    campo). Ela tem de valer como "usar o padrão": senão melhorar o padrão não chega a
    nenhum modelo existente."""
    antigo = orch.DEFAULT_TOOL_PROMPT
    novo = "NOVO PADRÃO de uso de ferramentas."
    monkeypatch.setattr(orch, "DEFAULT_TOOL_PROMPT", novo)
    monkeypatch.setattr(orch, "_STOCK_TOOL_PROMPTS", (antigo, novo))

    class Sift(_FakeSift):
        meta = {"catalog": [], "sift_mode": "prompt", "sift_prompt": "  " + antigo.replace(" ", "  ") + "\n"}

    asm = orch._assemble_tools_and_prompt(
        sift=Sift(), use_tools=True, code_mode=False, skills=[], genimage=None,
        kb_tool_on=False, kb_present=False, brain=None, skill_learning=False,
        subagents=[], run_subagent=None, native=orch.NativeToolOpts(),
    )

    assert novo in asm.sift_prompt
    assert antigo not in asm.sift_prompt


def test_prompt_personalizado_de_verdade_continua_valendo():
    assert not orch._is_stock_tool_prompt("Só use ferramentas quando eu pedir.")
    assert orch._is_stock_tool_prompt(orch.DEFAULT_TOOL_PROMPT)
