"""`reasoning_details` do OpenRouter: o raciocínio em blocos estruturados.

Além do texto corrido (`delta.reasoning`), o OpenRouter manda o raciocínio em blocos:
`reasoning.text` (com `signature` e `format`), `reasoning.encrypted` (opaco — OpenAI e
Gemini) e `reasoning.summary`. Os blocos precisam VOLTAR ao provedor na mensagem do
assistente que fez a chamada de ferramenta: Claude com pensamento estendido e Gemini 3
exigem as assinaturas para continuar depois do resultado da ferramenta, e os demais
modelos perdem o fio do que estavam raciocinando se o pensamento some.

As regras seguem o provedor oficial do OpenRouter para o AI SDK
(`@openrouter/ai-sdk-provider` 2.9.0, o mesmo que o opencode usa), para não reinventar:
- blocos de texto consecutivos se fundem num só (a assinatura chega no último delta);
- ao reenviar, sai o bloco de texto de formato Claude/Gemini SEM assinatura — o
  provedor recusaria o pedido inteiro. Sem `format`, o padrão é Claude.
"""
from __future__ import annotations

from typing import Any

_TEXT = "reasoning.text"
_SUMMARY = "reasoning.summary"
_DEFAULT_FORMAT = "anthropic-claude-v1"
_SIGNED_FORMATS = frozenset({"anthropic-claude-v1", "google-gemini-v1"})


def accumulate(acc: list[dict[str, Any]], details: Any) -> None:
    """Acrescenta os blocos de um delta a `acc`, fundindo texto consecutivo."""
    if not isinstance(details, list):
        return
    for detail in details:
        if not isinstance(detail, dict):
            continue
        last = acc[-1] if acc else None
        if detail.get("type") == _TEXT and last is not None and last.get("type") == _TEXT:
            last["text"] = (last.get("text") or "") + (detail.get("text") or "")
            last["signature"] = last.get("signature") or detail.get("signature")
            last["format"] = last.get("format") or detail.get("format")
        else:
            acc.append(dict(detail))


def text_of(details: Any) -> str:
    """Texto legível de um delta (para quando o provedor não manda `delta.reasoning`).
    Blocos cifrados não têm texto."""
    if not isinstance(details, list):
        return ""
    partes: list[str] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("type") == _TEXT:
            partes.append(detail.get("text") or "")
        elif detail.get("type") == _SUMMARY:
            partes.append(detail.get("summary") or "")
    return "".join(partes)


def history_entry(m: Any) -> dict[str, Any]:
    """Mensagem gravada → mensagem do histórico, com os blocos de raciocínio da resposta.

    Os blocos só valem para o modelo que os gerou (a assinatura do Claude é presa ao
    modelo; o cifrado da OpenAI também): o id dele vai junto em `_details_model`, e
    `for_model` decide no envio — o modelo do turno só é conhecido depois (agente "@",
    troca de modelo no chat)."""
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    r = getattr(m, "reasoning", None)
    if m.role == "assistant" and isinstance(r, dict) and r.get("details"):
        out["reasoning_details"] = r["details"]
        if r.get("details_text"):
            out["reasoning"] = r["details_text"]
        out["_details_model"] = r.get("details_model") or ""
    return out


def for_model(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Deixa no histórico só os blocos gerados por `model` e tira a marca interna.
    Mensagens do próprio turno (loop de ferramentas) não têm marca: são deste modelo."""
    out: list[dict[str, Any]] | None = None
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or "_details_model" not in m:
            continue
        limpo = {k: v for k, v in m.items() if k != "_details_model"}
        if m["_details_model"] != model:
            limpo.pop("reasoning_details", None)
            limpo.pop("reasoning", None)
        if out is None:
            out = list(messages)
        out[i] = limpo
    return messages if out is None else out


def replayable(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Os blocos que podem voltar ao provedor na próxima chamada."""
    out: list[dict[str, Any]] = []
    for detail in details:
        if detail.get("type") == _TEXT:
            formato = detail.get("format") or _DEFAULT_FORMAT
            if formato in _SIGNED_FORMATS and not detail.get("signature"):
                continue
        out.append({k: v for k, v in detail.items() if v is not None})
    return out
