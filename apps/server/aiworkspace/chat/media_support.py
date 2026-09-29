"""Mídia (imagem/áudio) que o modelo do turno aceita — e a degradação quando não aceita.

O chat troca de modelo entre turnos (seletor, "@", guarda de saída com fallback). Uma
foto mandada a um modelo com visão não pode travar a conversa quando o próximo modelo
não vê imagens: antes de enviar, as partes `image_url`/`input_audio` de TODAS as
mensagens viram uma linha de texto. Três fontes decidem se o modelo aceita:

1. a capacidade marcada no modelo (Visão/Áudio) — o chamador já a traz em MediaOpts;
2. o catálogo do OpenRouter, se já estiver em cache (`architecture.input_modalities`);
   nunca busca na rede aqui;
3. uma recusa anterior do provedor neste processo (memo por modelo).

Capacidade desconhecida → tenta com a mídia; se o provedor recusar por causa dela
(antes do 1º chunk), o orquestrador refaz UMA vez sem mídia e memoriza a recusa.
"""
from __future__ import annotations

import re
from typing import Any

IMAGE_PLACEHOLDER = "[imagem anexada — este modelo não vê imagens]"
AUDIO_PLACEHOLDER = "[áudio anexado — este modelo não ouve áudio]"

_PART_KIND = {"image_url": "image", "input_image": "image", "image": "image",
              "input_audio": "audio"}
_PLACEHOLDER = {"image": IMAGE_PLACEHOLDER, "audio": AUDIO_PLACEHOLDER}

# modelo → tipos de mídia que o provedor recusou (vale até reiniciar o processo)
_rejected: dict[str, set[str]] = {}


def _catalog_modalities(model: str) -> set[str] | None:
    """Modalidades de entrada do modelo segundo o catálogo JÁ em cache (sem rede)."""
    try:
        from ..providers import openrouter

        cached = openrouter._catalog_cache.get("all")
    except Exception:  # noqa: BLE001
        return None
    if not cached:
        return None
    for entry in cached[1] or []:
        if isinstance(entry, dict) and entry.get("id") == model:
            mods = (entry.get("architecture") or {}).get("input_modalities")
            return {str(m).lower() for m in mods} if isinstance(mods, list) and mods else None
    return None


def accepts(model: str | None, kind: str) -> bool:
    """O modelo aceita esta mídia ("image"/"audio")? Desconhecido → True."""
    if not model:
        return True
    if kind in _rejected.get(model, ()):
        return False
    mods = _catalog_modalities(model)
    return True if mods is None else kind in mods


def remember_rejection(model: str | None, kinds: set[str]) -> None:
    if model and kinds:
        _rejected.setdefault(model, set()).update(kinds)


def forget_rejections() -> None:
    _rejected.clear()


def media_kinds(messages: list[dict[str, Any]]) -> set[str]:
    """Tipos de mídia presentes (como partes) nas mensagens."""
    kinds: set[str] = set()
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for p in c:
                k = _PART_KIND.get(p.get("type")) if isinstance(p, dict) else None
                if k:
                    kinds.add(k)
    return kinds


def strip_media(messages: list[dict[str, Any]], kinds: set[str]) -> list[dict[str, Any]]:
    """Troca as partes de mídia dos tipos `kinds` por um texto curto, em TODAS as
    mensagens. Sobrando só texto, o conteúdo volta a ser string (provedores
    OpenAI-compatíveis simples aceitam melhor). Não muta a lista do chamador."""
    if not kinds:
        return messages
    out: list[dict[str, Any]] | None = None
    for i, m in enumerate(messages):
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        novas: list[Any] = []
        mudou = False
        for p in c:
            k = _PART_KIND.get(p.get("type")) if isinstance(p, dict) else None
            if k in kinds:
                novas.append({"type": "text", "text": _PLACEHOLDER[k]})
                mudou = True
            else:
                novas.append(p)
        if not mudou:
            continue
        if all(isinstance(p, dict) and p.get("type") == "text" and set(p) <= {"type", "text"}
               for p in novas):
            conteudo: Any = "\n\n".join(str(p.get("text") or "") for p in novas if p.get("text"))
        else:
            conteudo = novas
        if out is None:
            out = list(messages)
        out[i] = {**m, "content": conteudo}
    return messages if out is None else out


# erro do provedor por causa da mídia de ENTRADA (ex.: OpenRouter 404 "No endpoints
# found that support image input", Ollama "model does not support images", OpenAI
# "image_url is only supported by certain models"). "image output" (geração nativa)
# é outro caso — tratado pelo retry sem knobs.
_IMAGE_ERR = re.compile(
    r"image[ _-]?input|input[ _-]?images?|image_url|support(?:s|ed)?\s+(?:for\s+)?images?\b(?!\s*output)"
    r"|images?\s+(?:are|is)\s+not\s+supported|vision|multi-?modal",
    re.IGNORECASE,
)
_AUDIO_ERR = re.compile(
    r"audio[ _-]?input|input[ _-]?audio|support(?:s|ed)?\s+(?:for\s+)?audio\b(?!\s*output)"
    r"|audio\s+(?:is\s+)?not\s+supported",
    re.IGNORECASE,
)


def rejected_kinds(error: str) -> set[str]:
    """Que mídia de entrada o erro do provedor recusou (vazio = não é recusa de mídia)."""
    txt = error or ""
    kinds: set[str] = set()
    if _IMAGE_ERR.search(txt):
        kinds.add("image")
    if _AUDIO_ERR.search(txt):
        kinds.add("audio")
    return kinds
