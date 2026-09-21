"""Token de fim de turno vazado como texto: a resposta termina nele.

O caso real (Imaginai, DeepSeek): a narração chegou assim na tela —

    — Veste. Tamanho único, mas serve.<｜end▁of▁sentence｜>
    — Veste. Tamanho único, mas serve.

    Ela fica de costas enquanto você troca de roupa (...) Teve uma guerra. Depois,
    uma doença que veio junto com ela (...)

O modelo tinha encerrado a vez dele; o provedor devolveu o token como texto e deixou
a geração seguir. O que veio depois repetiu a última fala e respondeu perguntas que o
jogador ainda não tinha feito. O orquestrador até detectava o token, mas como
"possível tool-call vazada" — e, sem tool-call para resgatar, reemitia tudo.

Herméticos: o stream do provedor é simulado no nível do httpx.
"""
from __future__ import annotations

import json

import pytest

from aiworkspace.providers import openrouter, turn_end
from aiworkspace.providers.turn_end import TurnEndGuard, cut, scrub

EOS = "<｜end▁of▁sentence｜>"


def _alimenta(pedacos: list[str]) -> tuple[str, bool]:
    guard = TurnEndGuard()
    saida = "".join(guard.feed(p) for p in pedacos) + guard.flush()
    return saida, guard.ended


# --------------------------------------------------------------------------- #
# A guarda                                                                     #
# --------------------------------------------------------------------------- #
def test_resposta_termina_no_token_e_a_continuacao_some():
    saida, fim = _alimenta([
        "— Veste. Tamanho único, mas serve.",
        EOS + "\n— Veste. Tamanho único, mas serve.",
        "\n\nTeve uma guerra.",
    ])

    assert saida == "— Veste. Tamanho único, mas serve."
    assert fim


def test_token_partido_entre_chunks_nao_escapa():
    """O provedor pode entregar o token em pedaços; checar chunk a chunk (como a
    guarda antiga fazia) deixa passar tudo."""
    saida, fim = _alimenta(["serve.<｜end", "▁of▁sentence", "｜>\nTeve uma guerra."])

    assert saida == "serve."
    assert fim


def test_menor_que_comum_nao_fica_preso():
    """Reter o fim do chunk é só para o que PODE virar token. Um `<` de comparação
    ou de HTML tem de sair — no máximo um chunk atrasado."""
    saida, fim = _alimenta(["se a <", " b, então", " <b>negrito</b> e <|x"])

    assert saida == "se a < b, então <b>negrito</b> e <|x"
    assert not fim


@pytest.mark.parametrize("token", ["<|im_end|>", "<|eot_id|>", "<end_of_turn>", "<|endoftext|>"])
def test_outras_familias_de_modelo(token):
    saida, fim = _alimenta([f"resposta{token}", "continuação inventada"])

    assert saida == "resposta"
    assert fim


def test_token_citado_em_codigo_e_assunto_nao_fim():
    """Quem desenvolve com IA pergunta sobre esses tokens. Dentro de código, o
    token é conteúdo — cortar ali engoliria a explicação."""
    bloco = "O ChatML fecha cada mensagem assim:\n```\n<|im_start|>user\noi<|im_end|>\n```\nPronto."
    inline = "O token `<|im_end|>` fecha a mensagem."

    assert _alimenta([bloco]) == (bloco, False)
    assert _alimenta([inline]) == (inline, False)


def test_harmony_nao_e_cortado_aqui():
    """No Harmony, `<|end|>` fecha o raciocínio e a resposta final vem DEPOIS na
    mesma geração. Cortar ali entregaria o raciocínio no lugar da resposta."""
    texto = "<|channel|>analysis<|message|>pensando<|end|><|start|>assistant<|channel|>final<|message|>Oi!"

    assert cut(texto) == texto


def test_historico_perde_so_o_token():
    """A continuação antiga já foi lida e respondida pelo jogador ("doença? que
    doença?") — ela fica. Só o token sai, para o provedor não o ler como fim de
    turno no meio do histórico."""
    antiga = f"— Veste.{EOS}\n— Veste.\n\nTeve uma guerra. Depois, uma doença."

    assert scrub(antiga) == "— Veste.\n— Veste.\n\nTeve uma guerra. Depois, uma doença."


# --------------------------------------------------------------------------- #
# No stream do provedor                                                        #
# --------------------------------------------------------------------------- #
def _sse(delta: dict, finish: str | None = None) -> str:
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


class _Resp:
    status_code = 200
    linhas: list[str] = []
    lidas = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_lines(self):
        for linha in self.__class__.linhas:
            self.__class__.lidas += 1
            yield linha


class _Client:
    ultimo_json: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        self.__class__.ultimo_json = kwargs["json"]
        return _Resp()


async def _coleta(monkeypatch, linhas, mensagens=None):
    _Resp.linhas, _Resp.lidas = linhas, 0
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _Client)
    chunks = []
    async for c in openrouter.stream_chat(
        "key", "deepseek/deepseek-chat", mensagens or [{"role": "user", "content": "oi"}],
    ):
        chunks.append(c)
    texto = "".join(
        (ch.get("delta") or {}).get("content") or ""
        for c in chunks for ch in c.get("choices") or []
    )
    fins = [ch.get("finish_reason") for c in chunks for ch in c.get("choices") or [] if ch.get("finish_reason")]
    return texto, fins


@pytest.mark.asyncio
async def test_stream_para_no_fim_de_turno(monkeypatch):
    linhas = [
        _sse({"content": "— Veste. Tamanho único, mas serve."}),
        _sse({"content": "<｜end▁of"}),
        _sse({"content": "▁sentence｜>\n— Veste. Tamanho único, mas serve."}),
        _sse({"content": "\n\nTeve uma guerra."}),
        _sse({"content": " Depois, uma doença."}),
        _sse({}, finish="length"),
        "data: [DONE]",
    ]

    texto, fins = await _coleta(monkeypatch, linhas)

    assert texto == "— Veste. Tamanho único, mas serve."
    # a continuação nem é lida: fechar o stream faz o provedor parar de gerar
    assert _Resp.lidas == 3
    # e o orquestrador não vê "length" (resposta truncada) — o turno acabou normal
    assert fins == ["stop"]


@pytest.mark.asyncio
async def test_stream_normal_sai_intacto(monkeypatch):
    linhas = [
        _sse({"content": "a < b e "}),
        _sse({"content": "fim <"}),
        _sse({}, finish="stop"),
        "data: [DONE]",
    ]

    texto, fins = await _coleta(monkeypatch, linhas)

    assert texto == "a < b e fim <"
    assert fins == ["stop"]


@pytest.mark.asyncio
async def test_historico_enviado_ao_provedor_vai_sem_o_token(monkeypatch):
    mensagens = [
        {"role": "user", "content": "me dá uma roupa"},
        {"role": "assistant", "content": f"— Veste.{EOS}\n— Veste."},
        {"role": "user", "content": "o que é <|im_end|>?"},
    ]

    await _coleta(monkeypatch, [_sse({"content": "ok"}, finish="stop"), "data: [DONE]"], mensagens)

    enviadas = _Client.ultimo_json["messages"]
    textos = [m["content"] if isinstance(m["content"], str) else m["content"][0]["text"] for m in enviadas]
    assert EOS not in textos[1]
    # a mensagem do USUÁRIO não é tocada: citar o token é pergunta legítima
    assert "<|im_end|>" in textos[2]
    # e a lista do chamador continua como estava (o histórico salvo não muda)
    assert EOS in mensagens[1]["content"]


def test_completion_sem_stream_tambem_corta():
    """Títulos, roteadores e juízes usam a completion sem stream."""
    assert cut(f"Título do chat{EOS}Outro título") == "Título do chat"
    assert turn_end.cut("sem token nenhum") == "sem token nenhum"


# --------------------------------------------------------------------------- #
# Fim de turno dentro do RACIOCÍNIO                                            #
# --------------------------------------------------------------------------- #
# O caso real (Ayala, DeepSeek V4 Flash, raciocínio "médio"): em 14 de 64 respostas a
# cena inteira foi para o "Pensou por…" terminando em <｜end▁of▁sentence｜>, e a
# resposta visível era a continuação que o provedor não parou — às vezes uma cópia,
# às vezes outra versão, às vezes só um fecho de duas linhas.
CENA = "A porta abre. Lea entra sozinha.\n\n— A escolha é sua. O que você decide?"


@pytest.mark.asyncio
async def test_stream_para_quando_o_raciocinio_encerra_o_turno(monkeypatch):
    linhas = [
        _sse({"reasoning": "A porta abre. Lea entra sozinha.\n\n"}),
        _sse({"reasoning": "— A escolha é sua. O que você decide?<｜end▁of"}),
        _sse({"reasoning": "▁sentence｜>"}),
        _sse({"content": "Você olha para ela. A escolha está na sua frente."}),
        _sse({}, finish="stop"),
        "data: [DONE]",
    ]
    _Resp.linhas, _Resp.lidas = linhas, 0
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _Client)

    chunks = [c async for c in openrouter.stream_chat("key", "deepseek/x", [{"role": "user", "content": "oi"}])]

    pensamento = "".join((ch.get("delta") or {}).get("reasoning") or "" for c in chunks for ch in c["choices"])
    texto = "".join((ch.get("delta") or {}).get("content") or "" for c in chunks for ch in c["choices"])
    assert pensamento == CENA
    assert texto == ""  # o fecho pós-fim-de-turno nem foi lido
    assert _Resp.lidas == 3
    assert chunks[-1]["choices"][0]["turn_end_in_reasoning"] is True


@pytest.mark.asyncio
async def test_menor_que_retido_no_raciocinio_nao_se_perde(monkeypatch):
    """O raciocínio pode terminar num `<` (ex.: "se x <") logo antes da resposta."""
    linhas = [
        _sse({"reasoning": "vale se x <"}),
        _sse({"content": "Resposta."}),
        _sse({}, finish="stop"),
        "data: [DONE]",
    ]
    _Resp.linhas, _Resp.lidas = linhas, 0
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _Client)

    chunks = [c async for c in openrouter.stream_chat("key", "m/x", [{"role": "user", "content": "oi"}])]

    pensamento = "".join((ch.get("delta") or {}).get("reasoning") or "" for c in chunks for ch in c["choices"])
    assert pensamento == "vale se x <"


def test_turno_inteiro_promove_o_raciocinio_a_resposta(monkeypatch):
    """Ponta a ponta (provedor real com HTTP simulado → run_turn → done): a cena sai
    do raciocínio e vira a resposta; o fecho inventado depois do fim de turno some."""
    import asyncio

    from aiworkspace.chat.orchestrator import TurnSession, run_turn

    _Resp.linhas, _Resp.lidas = [
        _sse({"reasoning": CENA + EOS}),
        _sse({"content": "Você olha para ela. A escolha está na sua frente."}),
        _sse({}, finish="stop"),
        "data: [DONE]",
    ], 0
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _Client)

    async def go():
        return [ev async for ev in run_turn(
            api_key="k", model="deepseek/x", history=[], user_text="pode entrar",
            chat_system_prompt=None, params={}, use_tools=False, sift=None,
            session=TurnSession(user_id="u"))]

    eventos = asyncio.run(go())
    done = next(e for e in eventos if e["type"] == "done")

    assert done["content"] == CENA
    assert "escolha está na sua frente" not in done["content"]
    # nada sobra no "Pensou por…": aquele bloco inteiro era a resposta
    assert not (done.get("reasoning") or {}).get("text")
    assert any(e["type"] == "reasoning_answer" for e in eventos)


def test_raciocinio_normal_continua_raciocinio(monkeypatch):
    """Sem fim de turno no raciocínio, nada muda: pensamento fica no "Pensou por…"."""
    import asyncio

    from aiworkspace.chat.orchestrator import TurnSession, run_turn

    _Resp.linhas, _Resp.lidas = [
        _sse({"reasoning": "O jogador abriu a porta. Narrar a entrada da Lea."}),
        _sse({"content": CENA}),
        _sse({}, finish="stop"),
        "data: [DONE]",
    ], 0
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", _Client)

    async def go():
        return [ev async for ev in run_turn(
            api_key="k", model="deepseek/x", history=[], user_text="pode entrar",
            chat_system_prompt=None, params={}, use_tools=False, sift=None,
            session=TurnSession(user_id="u"))]

    eventos = asyncio.run(go())
    done = next(e for e in eventos if e["type"] == "done")

    assert done["content"] == CENA
    assert done["reasoning"]["text"] == "O jogador abriu a porta. Narrar a entrada da Lea."
    assert not any(e["type"] == "reasoning_answer" for e in eventos)


def test_gravacao_tira_o_bloco_promovido_das_etapas():
    """O "Pensou por…" salvo é montado pelo ActivityTrace a partir dos eventos."""
    from aiworkspace.chat.activity import ActivityTrace

    trace = ActivityTrace()
    trace.add({"type": "reasoning", "text": CENA})
    trace.add({"type": "reasoning_answer", "text": CENA})
    trace.add({"type": "token", "text": CENA})

    assert trace.reasoning(None) is None
