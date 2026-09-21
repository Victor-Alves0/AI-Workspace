"""Tokens de fim de turno que vazam como texto.

Um provedor com o template do modelo mal configurado devolve o token de fim de turno
como CONTEÚDO (`<｜end▁of▁sentence｜>` no DeepSeek, `<|im_end|>` no ChatML...) e não
para a geração. O modelo já encerrou a vez dele; o que vem depois é uma continuação
sem pergunta nenhuma — repete a última fala e segue respondendo o que o usuário ainda
não perguntou. Num roleplay isso aparece como um despejo de lore do nada.

O token marca o fim da resposta, então cortamos ali: o texto anterior é a resposta,
o posterior é descartado. Vale para o stream (o token pode chegar partido entre dois
chunks) e para as completions sem stream.

Os tokens do Harmony (`<|end|>`, `<|start|>`, `<|channel|>`...) NÃO entram: lá um
`<|end|>` fecha o canal de raciocínio e a resposta final vem DEPOIS, na mesma
geração. Eles têm tratamento próprio no resgate de tool-calls do orquestrador.
"""
from __future__ import annotations

import re

END_TOKENS: tuple[str, ...] = (
    # DeepSeek (barra U+FF5C, espaço U+2581) — e a variante com barra ASCII
    "<｜end▁of▁sentence｜>", "<｜begin▁of▁sentence｜>", "<｜User｜>", "<｜Assistant｜>",
    "<|end▁of▁sentence|>", "<|begin▁of▁sentence|>",
    # ChatML (Qwen, Yi, Hermes...)
    "<|im_end|>", "<|im_start|>", "<|endoftext|>",
    # Llama 3
    "<|eot_id|>", "<|end_of_text|>", "<|start_header_id|>",
    # Gemma
    "<end_of_turn>", "<start_of_turn>",
    # GLM / Phi
    "<|user|>", "<|assistant|>",
)

_END_RE = re.compile("|".join(re.escape(t) for t in END_TOKENS))


def _is_partial(tail: str) -> bool:
    """`tail` pode ser o começo de um token que termina no próximo chunk?"""
    return any(t.startswith(tail) and t != tail for t in END_TOKENS)


def _in_code(text: str) -> bool:
    """Um token citado em código é assunto da conversa, não fim de turno (ex.: o
    usuário perguntando o que é `<|im_end|>`)."""
    if text.count("```") % 2:
        return True
    return text.rsplit("\n", 1)[-1].count("`") % 2 == 1


class TurnEndGuard:
    """Filtra o texto de UMA resposta em stream, cortando no fim de turno.

    `feed` devolve o que já pode ser mostrado; o fim de um chunk que ainda pode
    virar token fica retido até o próximo. `ended` avisa que o resto é descartável.
    """

    def __init__(self) -> None:
        self.ended = False
        self._held = ""
        self._emitted = ""

    def feed(self, text: str) -> str:
        if self.ended or not text:
            return ""
        buf = self._held + text
        self._held = ""
        out: list[str] = []
        pos = 0
        while True:
            m = _END_RE.search(buf, pos)
            if m is None:
                break
            antes = buf[pos:m.start()]
            if _in_code(self._emitted + "".join(out) + antes):
                out.append(buf[pos:m.end()])
                pos = m.end()
                continue
            out.append(antes)
            self.ended = True
            return self._commit(out)
        resto = buf[pos:]
        corte = resto.rfind("<")
        if corte != -1 and _is_partial(resto[corte:]):
            self._held = resto[corte:]
            resto = resto[:corte]
        out.append(resto)
        return self._commit(out)

    def flush(self) -> str:
        """Fim do stream: o que estava retido não era token, é texto."""
        if self.ended:
            return ""
        held, self._held = self._held, ""
        self._emitted += held
        return held

    def _commit(self, out: list[str]) -> str:
        s = "".join(out)
        self._emitted += s
        return s


def cut(text: str) -> str:
    """Versão sem stream: a resposta até o primeiro fim de turno."""
    guard = TurnEndGuard()
    return guard.feed(text) + guard.flush()


def scrub(text: str) -> str:
    """Tira o token do HISTÓRICO sem cortar nada.

    A continuação que vazou numa resposta antiga já foi lida pelo usuário e virou
    parte da conversa — cortá-la agora faria o modelo esquecer algo que o usuário
    respondeu. Só o token sai: reenviado, o provedor pode tokenizá-lo como o token
    especial de verdade e encerrar o turno no meio do histórico."""
    return _END_RE.sub("", text)
