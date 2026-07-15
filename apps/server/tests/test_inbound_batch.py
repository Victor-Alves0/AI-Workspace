"""Agregação de entrada dos canais: janela de silêncio + lock por conversa.

Duas garantias distintas, testadas separadas:
- DEBOUNCE: mensagens fragmentadas do mesmo remetente viram UM turno (1 chamada ao
  modelo em vez de N), e a janela reinicia a cada mensagem nova.
- LOCK: dois turnos NUNCA rodam concorrentes na mesma conversa (era a corrida do
  Discord, cujo gateway despacha cada mensagem numa task).
"""
from __future__ import annotations

import asyncio

from aiworkspace.integrations import inbound_batch as ib

WIN = 0.15  # janela curta p/ o teste


def _msg(t: str) -> dict:
    return {"text": t}


async def test_no_window_runs_immediately():
    got: list[list[dict]] = []

    async def runner(batch):
        got.append(batch)

    await ib.submit(convo="c1", sender="s", msg=_msg("oi"), seconds=0, runner=runner)
    assert got == [[{"text": "oi"}]]  # sem janela = comportamento antigo, na hora


async def test_fragments_become_one_turn():
    got: list[list[dict]] = []

    async def runner(batch):
        got.append(batch)

    for t in ("oi", "tudo bem?", "queria saber X"):
        await ib.submit(convo="c2", sender="s", msg=_msg(t), seconds=WIN, runner=runner)
        await asyncio.sleep(WIN / 3)  # digitando rápido: dentro da janela

    assert got == []  # ainda em silêncio-pendente: nada rodou
    await asyncio.sleep(WIN * 2)
    assert len(got) == 1                                   # UM turno, não três
    assert [m["text"] for m in got[0]] == ["oi", "tudo bem?", "queria saber X"]


async def test_window_restarts_on_each_message():
    got: list[list[dict]] = []

    async def runner(batch):
        got.append(batch)

    await ib.submit(convo="c3", sender="s", msg=_msg("a"), seconds=WIN, runner=runner)
    await asyncio.sleep(WIN * 0.8)          # quase disparando...
    await ib.submit(convo="c3", sender="s", msg=_msg("b"), seconds=WIN, runner=runner)
    await asyncio.sleep(WIN * 0.8)          # ...reiniciou: ainda não pode ter disparado
    assert got == []
    await asyncio.sleep(WIN * 1.5)
    assert len(got) == 1 and len(got[0]) == 2


async def test_silence_closes_the_batch():
    got: list[list[dict]] = []

    async def runner(batch):
        got.append(batch)

    await ib.submit(convo="c4", sender="s", msg=_msg("a"), seconds=WIN, runner=runner)
    await asyncio.sleep(WIN * 2)            # silêncio → dispara
    await ib.submit(convo="c4", sender="s", msg=_msg("b"), seconds=WIN, runner=runner)
    await asyncio.sleep(WIN * 2)            # nova janela → segundo turno
    assert [[m["text"] for m in b] for b in got] == [["a"], ["b"]]


async def test_senders_do_not_mix_in_a_group():
    """Num grupo, a fala de duas pessoas não pode virar um turno costurado."""
    got: list[list[dict]] = []

    async def runner(batch):
        got.append(batch)

    await ib.submit(convo="grupo", sender="ana", msg=_msg("oi"), seconds=WIN, runner=runner)
    await ib.submit(convo="grupo", sender="bob", msg=_msg("eae"), seconds=WIN, runner=runner)
    await asyncio.sleep(WIN * 2)
    assert len(got) == 2                                   # um turno por remetente
    assert {b[0]["text"] for b in got} == {"oi", "eae"}


async def test_batch_has_a_ceiling(monkeypatch):
    """Um flood não pode virar um turno gigante: ao atingir o teto, dispara na hora."""
    monkeypatch.setattr(ib, "MAX_BATCH", 3)
    got: list[list[dict]] = []

    async def runner(batch):
        got.append(batch)

    for i in range(3):
        await ib.submit(convo="c5", sender="s", msg=_msg(str(i)), seconds=WIN, runner=runner)
    assert len(got) == 1 and len(got[0]) == 3              # disparou sem esperar o silêncio


async def test_same_conversation_never_runs_concurrently():
    """A corrida do Discord: duas mensagens rápidas no mesmo canal."""
    running = 0
    overlap = False

    async def runner(batch):
        nonlocal running, overlap
        running += 1
        if running > 1:
            overlap = True
        await asyncio.sleep(0.05)           # o "turno" demora
        running -= 1

    # sem janela (seconds=0) as duas entram direto — como o gateway do Discord faz
    await asyncio.gather(
        ib.submit(convo="canal", sender="a", msg=_msg("1"), seconds=0, runner=runner),
        ib.submit(convo="canal", sender="a", msg=_msg("2"), seconds=0, runner=runner),
    )
    assert not overlap, "dois turnos rodaram concorrentes na mesma conversa"


async def test_different_conversations_do_run_in_parallel():
    """O lock é por CONVERSA — não pode serializar o canal inteiro."""
    active = 0
    peak = 0

    async def runner(batch):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1

    await asyncio.gather(*[
        ib.submit(convo=f"conv{i}", sender="a", msg=_msg("x"), seconds=0, runner=runner)
        for i in range(3)
    ])
    assert peak == 3


async def test_runner_failure_does_not_leak():
    """Uma conversa que explode não pode derrubar o canal nem travar o lock."""
    async def boom(batch):
        raise RuntimeError("falhou")

    await ib.submit(convo="c6", sender="s", msg=_msg("x"), seconds=0, runner=boom)
    assert not ib.lock("c6").locked()      # o lock foi liberado

    got: list[list[dict]] = []

    async def ok(batch):
        got.append(batch)

    await ib.submit(convo="c6", sender="s", msg=_msg("y"), seconds=0, runner=ok)
    assert got == [[{"text": "y"}]]        # a conversa segue viva


def test_window_is_clamped():
    assert ib.window(None) == 0
    assert ib.window("nao-e-numero") == 0
    assert ib.window(-5) == 0
    assert ib.window(3) == 3
    assert ib.window(9999) == ib.MAX_WINDOW_SECONDS   # ninguém debounce por horas
