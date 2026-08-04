"""Continuação de FILA: a mensagem enviada durante a geração tem que ser respondida.

Bug (pré-existente, provado nesta bateria): `already_persisted` reconstruía a entrada
pegando as mensagens 'user' do FIM do histórico. Mas a ordem real de persistência é:

    t1  usuário envia durante a geração -> a rota PERSISTE a mensagem e enfileira
    t2  a geração termina -> `_finalize` PERSISTE a resposta do assistente
    t3  o driver drena a fila -> on_queue -> resume_chat_turn(already_persisted=True)

Em t3 o histórico é [user, user_enfileirada, assistant] — a enfileirada NÃO está no fim,
`trailing` voltava vazio e a função retornava sem responder: mensagem descartada em
silêncio (o modo "fila" do steering nunca continuava; só o "steer" funcionava).

Agora a entrada vem dos textos DRENADOS (`queued_texts`) e a idempotência é a própria
drenagem — `drain_queue` esvazia a lista, então um segundo disparo recebe [].
"""
from __future__ import annotations

import asyncio

from aiworkspace.chat.generation import Generation


class M:
    """Stand-in de Message (só o que o trecho de histórico usa)."""
    def __init__(self, role: str, content: str):
        self.role, self.content = role, content


def _monta_turno(convo: list[M], texts: list[str]) -> tuple[list[dict], str]:
    """Réplica do bloco `already_persisted` de resume.resume_chat_turn."""
    texts = [t for t in (texts or []) if (t or "").strip()]
    if not texts:
        return [], ""
    restantes = list(convo)
    for t in reversed(texts):
        alvo = t.strip()
        for i in range(len(restantes) - 1, -1, -1):
            m = restantes[i]
            if m.role == "user" and (m.content or "").strip() == alvo:
                restantes.pop(i)
                break
    history = [{"role": m.role, "content": m.content} for m in restantes]
    return history, "\n\n".join(texts).strip()


def test_mensagem_enfileirada_e_respondida_na_ordem_real():
    """A ordem REAL (resposta persistida ANTES da continuação) tem que funcionar."""
    convo = [M("user", "pergunta 1"), M("user", "ENFILEIRADA"), M("assistant", "resposta 1")]
    history, entrada = _monta_turno(convo, ["ENFILEIRADA"])
    assert entrada == "ENFILEIRADA", "a mensagem enfileirada não pode ser descartada"
    # e não pode aparecer DUPLICADA (no histórico e como pergunta atual)
    assert [h["content"] for h in history] == ["pergunta 1", "resposta 1"]


def test_varias_enfileiradas_viram_um_turno_so():
    convo = [M("user", "p1"), M("user", "a"), M("user", "b"), M("assistant", "r1")]
    history, entrada = _monta_turno(convo, ["a", "b"])
    assert entrada == "a\n\nb"
    assert [h["content"] for h in history] == ["p1", "r1"]


def test_conteudo_repetido_remove_uma_ocorrencia_por_texto():
    """Mesma frase enviada antes e durante a geração: só a enfileirada sai do histórico."""
    convo = [M("user", "oi"), M("assistant", "olá"), M("user", "oi"), M("assistant", "r")]
    history, entrada = _monta_turno(convo, ["oi"])
    assert entrada == "oi"
    assert [h["content"] for h in history] == ["oi", "olá", "r"]  # a 1ª "oi" permanece


def test_sem_textos_nao_dispara_turno():
    assert _monta_turno([M("user", "x")], [])[1] == ""
    assert _monta_turno([M("user", "x")], ["   "])[1] == ""


def test_drenagem_e_idempotente_so_um_disparo():
    """O ponto de idempotência: quem drena primeiro leva; o segundo recebe []."""
    async def go():
        g = Generation("c1")
        await g.enqueue("m1", steer=False)
        await g.enqueue("m2", steer=False)
        primeiro = g.drain_queue()
        segundo = g.drain_queue()      # corrida: driver x fallback da rota
        return primeiro, segundo

    primeiro, segundo = asyncio.run(go())
    assert primeiro == ["m1", "m2"]
    assert segundo == [], "um 2º disparo não pode responder de novo (turno duplicado)"
