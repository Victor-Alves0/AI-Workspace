"""Rolagem de dados no estilo roll20 — global (comando //roll do chat e ferramentas).

Sintaxe aceita (sem diferenciar maiúsculas, espaços ignorados):
    1d20+2        d20 (= 1d20)      2d6+1d4+3      4d6kh3 (mantém os 3 maiores)
    2d20kh1 (vantagem)   2d20kl1 (desvantagem)   1d8-1     8 (constante)

O servidor rola (com `secrets`) e devolve o detalhe de cada dado — o jogador vê o que
saiu, não só o total. Limites impedem expressão que trave o servidor (1000d1000...).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field

Roller = Callable[[int], int]

MAX_TERMS = 12
MAX_DICE = 100
MAX_SIDES = 1000

_TERM = re.compile(r"([+-])?\s*(?:(\d*)d(\d+)(?:(kh|kl)(\d+))?|(\d+))", re.IGNORECASE)


class DiceError(ValueError):
    """Expressão inválida — a mensagem volta ao usuário/modelo."""


def secure_roller(sides: int) -> int:
    return secrets.randbelow(max(1, sides)) + 1


@dataclass
class Term:
    sign: int
    count: int = 0
    sides: int = 0
    keep: str | None = None        # "kh" | "kl"
    keep_n: int = 0
    constant: int = 0
    rolls: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)

    @property
    def value(self) -> int:
        return self.sign * (sum(self.kept) if self.count else self.constant)

    def label(self) -> str:
        if not self.count:
            return str(self.constant)
        keep = f"{self.keep}{self.keep_n}" if self.keep else ""
        return f"{self.count}d{self.sides}{keep}"


@dataclass
class RollResult:
    expression: str
    total: int
    terms: list[Term]

    def breakdown(self) -> str:
        """`1d20+2` → "[14] + 2" ; dados descartados aparecem riscados (~~3~~)."""
        partes = []
        for i, term in enumerate(self.terms):
            sinal = ("-" if term.sign < 0 else "+") if i else ("-" if term.sign < 0 else "")
            if term.count:
                restantes = list(term.kept)
                itens = []
                for r in term.rolls:
                    if r in restantes:
                        restantes.remove(r)
                        itens.append(str(r))
                    else:
                        itens.append(f"~~{r}~~")
                corpo = "[" + ", ".join(itens) + "]"
            else:
                corpo = str(term.constant)
            partes.append(f"{sinal} {corpo}".strip() if i else f"{sinal}{corpo}")
        return " ".join(partes)

    def as_dict(self) -> dict:
        return {
            "expression": self.expression, "total": self.total, "breakdown": self.breakdown(),
            "terms": [{"term": t.label(), "sign": t.sign, "rolls": t.rolls, "kept": t.kept,
                       "value": t.value} for t in self.terms],
        }


def parse(expression: str) -> list[Term]:
    text = (expression or "").replace(" ", "").lower()
    if not text:
        raise DiceError("Informe os dados, ex.: 1d20+2")
    pos, terms = 0, []
    while pos < len(text):
        match = _TERM.match(text, pos)
        if not match or match.end() == pos:
            raise DiceError(f"Não entendi '{text[pos:]}'. Exemplos: 1d20+2, 2d6+3, 4d6kh3, 2d20kh1")
        sign_raw, count_raw, sides_raw, keep, keep_n, constant = match.groups()
        if terms and not sign_raw:
            raise DiceError("Separe os termos com + ou -")
        sign = -1 if sign_raw == "-" else 1
        if sides_raw is not None:
            count = int(count_raw) if count_raw else 1
            sides = int(sides_raw)
            if not 1 <= count <= MAX_DICE:
                raise DiceError(f"Até {MAX_DICE} dados por termo.")
            if not 2 <= sides <= MAX_SIDES:
                raise DiceError(f"O dado precisa ter de 2 a {MAX_SIDES} lados.")
            n = int(keep_n) if keep_n else 0
            if keep and not 1 <= n <= count:
                raise DiceError("kh/kl precisa manter entre 1 e o número de dados.")
            terms.append(Term(sign=sign, count=count, sides=sides, keep=keep, keep_n=n))
        else:
            terms.append(Term(sign=sign, constant=int(constant)))
        if len(terms) > MAX_TERMS:
            raise DiceError(f"No máximo {MAX_TERMS} termos.")
        pos = match.end()
    return terms


def roll(expression: str, roller: Roller = secure_roller) -> RollResult:
    terms = parse(expression)
    for term in terms:
        if not term.count:
            continue
        term.rolls = [roller(term.sides) for _ in range(term.count)]
        if term.keep == "kh":
            term.kept = sorted(term.rolls, reverse=True)[:term.keep_n]
        elif term.keep == "kl":
            term.kept = sorted(term.rolls)[:term.keep_n]
        else:
            term.kept = list(term.rolls)
    canonical = "".join(
        (("-" if t.sign < 0 else "+") if i else ("-" if t.sign < 0 else "")) + t.label()
        for i, t in enumerate(terms)
    )
    return RollResult(expression=canonical, total=sum(t.value for t in terms), terms=terms)


def chat_line(result: RollResult, label: str = "") -> str:
    """Texto da rolagem como aparece na conversa (e no contexto do modelo)."""
    titulo = f" — {label}" if label else ""
    return f"🎲 **{result.expression}**{titulo}: {result.breakdown()} = **{result.total}**"
