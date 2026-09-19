"""Dados estilo roll20 (aiworkspace/dice.py) — usados pelo //roll do chat e pelo Imaginai."""
from __future__ import annotations

import pytest

from aiworkspace import dice


def _dado(*valores):
    fila = list(valores)
    return lambda sides: fila.pop(0)


def test_d20_mais_modificador():
    r = dice.roll("1d20+2", _dado(14))
    assert r.total == 16 and r.breakdown() == "[14] + 2"
    assert r.expression == "1d20+2"


def test_d20_sem_quantidade_e_espacos():
    assert dice.roll(" d20 + 5 ", _dado(10)).total == 15


def test_varios_termos_e_subtracao():
    r = dice.roll("2d6+1d4-1", _dado(3, 5, 2))
    assert r.total == 9 and r.breakdown() == "[3, 5] + [2] - 1"


def test_manter_maiores_e_menores():
    assert dice.roll("4d6kh3", _dado(1, 6, 5, 4)).total == 15
    assert dice.roll("2d20kh1", _dado(7, 18)).total == 18        # vantagem
    assert dice.roll("2d20kl1", _dado(7, 18)).total == 7         # desvantagem


def test_descartado_aparece_riscado():
    assert "~~1~~" in dice.roll("4d6kh3", _dado(1, 6, 5, 4)).breakdown()


def test_constante_sozinha():
    assert dice.roll("8", _dado()).total == 8


@pytest.mark.parametrize("expr", ["", "abc", "1d1", "1000d6", "1d20++2", "3d6kh5", "1d20 x"])
def test_expressoes_invalidas_explicam(expr):
    with pytest.raises(dice.DiceError):
        dice.roll(expr, _dado(*[1] * 50))


def test_linha_do_chat():
    linha = dice.chat_line(dice.roll("1d20+2", _dado(14)), "Furtividade")
    assert linha == "🎲 **1d20+2** — Furtividade: [14] + 2 = **16**"


def test_rolagem_real_fica_no_intervalo():
    for _ in range(200):
        assert 1 <= dice.roll("1d20").total <= 20
