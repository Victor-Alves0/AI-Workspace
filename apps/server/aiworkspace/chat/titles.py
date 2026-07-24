"""Geração de título curto para novos chats (opt-in, configurável por usuário).

Uma única chamada barata ao modelo, feita SÓ na primeira troca do chat e apenas
quando o usuário liga "Gerar título de novos chats" (perfil → Interface). O modelo
e o system prompt são configuráveis; em branco, usa o padrão abaixo.
"""

from __future__ import annotations

import logging

from ..providers import openrouter

logger = logging.getLogger(__name__)

# limite rígido do título (também comunicado ao modelo no prompt)
MAX_TITLE_CHARS = 60

# Orçamento de saída da chamada de título. NÃO reduzir: em modelos de RACIOCÍNIO
# (DeepSeek v4, o1/o3 e afins) os tokens de reasoning saem ANTES do conteúdo e
# consomem o teto — com 32/64/128 o `content` volta VAZIO e o chat fica com o
# fallback (a 1ª mensagem crua truncada, ex.: uma URL do YouTube). Medido: 32/64/128
# → ""; 512 → "Prova matematica de 1=2". O custo extra é irrelevante (1 chamada/chat)
# porque o título real continua tendo no máximo MAX_TITLE_CHARS.
TITLE_MAX_TOKENS = 512

# system prompt padrão (usado quando o usuário deixa o campo em branco). O
# tamanho máximo é injetado via {max}.
DEFAULT_TITLE_PROMPT = (
    "You write a very short, descriptive title for a chat conversation. "
    "Rules: at most {max} characters; a few words only; same language as the user; "
    "no surrounding quotes, no trailing punctuation, no emoji, no prefix like 'Title:'. "
    "Reply with ONLY the title text."
)


def _clean(raw: str) -> str:
    t = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    t = t.strip().strip("\"'“”‘’`").strip()
    for pre in ("title:", "título:", "titulo:"):
        if t.lower().startswith(pre):
            t = t[len(pre):].strip()
    return t.rstrip(" .:-").strip()


async def generate_title(
    api_key: str,
    model: str,
    user_text: str,
    assistant_text: str = "",
    custom_prompt: str = "",
    *,
    base_url: str | None = None,
) -> str:
    """Gera um título curto a partir da primeira troca. Devolve "" se falhar."""
    system = (custom_prompt or "").strip() or DEFAULT_TITLE_PROMPT
    system = system.replace("{max}", str(MAX_TITLE_CHARS))
    # garante que o limite seja comunicado mesmo em prompts customizados
    if str(MAX_TITLE_CHARS) not in system:
        system = f"{system}\n\nHard limit: at most {MAX_TITLE_CHARS} characters."

    convo = f"User: {(user_text or '').strip()[:1500]}"
    if assistant_text:
        convo += f"\n\nAssistant: {assistant_text.strip()[:1500]}"
    convo += "\n\nTitle:"

    try:
        out = await openrouter.complete(
            api_key,
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": convo},
            ],
            params={"max_tokens": TITLE_MAX_TOKENS, "temperature": 0.3},
            timeout=30.0,
            base_url=base_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Geração de título falhou (%s); mantém o título atual", exc)
        return ""
    return _clean(out)[:MAX_TITLE_CHARS]
