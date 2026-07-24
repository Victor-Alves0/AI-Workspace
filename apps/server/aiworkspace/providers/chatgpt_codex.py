"""Adaptador Codex: fala a Responses API do backend do ChatGPT e apresenta a
MESMA interface de chunks (deltas estilo chat/completions) que o resto do app
consome — o orchestrator não sabe que o provedor mudou.

Modelos `codex/<id>` (assinatura ChatGPT) são roteados p/ cá pelo
`openrouter.py` (mesmo truque do prefixo `ollama/`). A "api_key" desses
modelos é o sentinela `codex:<user_id>` (resolvido no turn_setup): o token
real mora cifrado no banco e é buscado/renovado por chamada em
`chatgpt_service.get_access` — nunca viaja nos kwargs do turno.

Tradução:
  entrada  chat messages → input items (developer/user/assistant; tool calls →
           function_call / function_call_output)
  tools    [{type:function, function:{...}}] → [{type:function, name, ...}]
  saída    response.output_text.delta        → delta.content
           response.reasoning_summary_text.delta → delta.reasoning
           response.output_item.done(function_call) → delta.tool_calls
           response.completed                → chunk final c/ usage
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MODEL_PREFIX = "codex/"
API_KEY_PREFIX = "codex:"
BASE_URL = "https://chatgpt.com/backend-api/codex/responses"


def is_codex_model(model: str | None) -> bool:
    return bool(model) and model.startswith(MODEL_PREFIX)


def _bare_model(model: str) -> str:
    return model.removeprefix(MODEL_PREFIX)


def _user_id_from_key(api_key: str) -> str:
    """A api_key dos modelos codex é `codex:<uid>`. Qualquer outra coisa =
    chamada veio de um caminho que não resolveu o provedor — erro claro em vez
    de um 401 misterioso da OpenAI."""
    if not (api_key or "").startswith(API_KEY_PREFIX):
        raise RuntimeError(
            "modelo codex/* precisa da conexão ChatGPT (Conexões → Assinaturas) "
            "— provedor não resolvido neste caminho"
        )
    return api_key[len(API_KEY_PREFIX):]


# --------------------------------------------------------------------------- #
# chat messages → input items da Responses API
# --------------------------------------------------------------------------- #
def _text_of(content: Any) -> str:
    """Achata o content (str ou lista de parts) em texto. Imagens/áudio não
    passam pelo backend do Codex — os Routers (Vision/Audio) já convertem antes."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text") or "")
        return "\n".join(x for x in out if x)
    return "" if content is None else str(content)


def build_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role") or "user"
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id") or "",
                "output": _text_of(m.get("content")),
            })
            continue
        if role == "assistant" and m.get("tool_calls"):
            txt = _text_of(m.get("content"))
            if txt:
                items.append({"role": "assistant",
                              "content": [{"type": "output_text", "text": txt}]})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": fn.get("arguments") or "{}",
                })
            continue
        text = _text_of(m.get("content"))
        if not text:
            continue
        if role in ("system", "developer"):
            items.append({"role": "developer",
                          "content": [{"type": "input_text", "text": text}]})
        elif role == "assistant":
            items.append({"role": "assistant",
                          "content": [{"type": "output_text", "text": text}]})
        else:
            items.append({"role": "user",
                          "content": [{"type": "input_text", "text": text}]})
    return items


def convert_tools(tools: list[dict] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools or []:
        fn = t.get("function") or {}
        if not fn.get("name"):
            continue
        out.append({
            "type": "function",
            "name": fn["name"],
            "description": fn.get("description") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            "strict": False,
        })
    return out


def _reasoning_effort(params: dict[str, Any] | None) -> str:
    """`reasoning` do app ({effort: low|medium|high}) → effort do Codex."""
    r = (params or {}).get("reasoning")
    if isinstance(r, dict) and r.get("effort") in ("minimal", "low", "medium", "high"):
        return r["effort"]
    return "medium"


def build_payload(model: str, messages: list[dict[str, Any]], *,
                  instructions: str, tools: list[dict] | None = None,
                  params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": _bare_model(model),
        "instructions": instructions,
        "input": build_input(messages),
        "store": False,
        "stream": True,
        "reasoning": {"effort": _reasoning_effort(params), "summary": "auto"},
        "include": [],
    }
    conv = convert_tools(tools)
    if conv:
        payload["tools"] = conv
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = False
    return payload


# --------------------------------------------------------------------------- #
# Stream SSE (Responses) → chunks estilo chat/completions
# --------------------------------------------------------------------------- #
async def stream_chat(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict] | None = None,
    params: dict[str, Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    from ..integrations import chatgpt_service

    user_id = _user_id_from_key(api_key)
    access, account_id = await chatgpt_service.get_access(user_id)
    instructions = await chatgpt_service.get_instructions()
    payload = build_payload(model, messages, instructions=instructions,
                            tools=tools, params=params)
    headers = {
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "session_id": user_id,
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id

    tool_idx = 0
    timeout = httpx.Timeout(connect=15.0, write=30.0, read=300.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", BASE_URL, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                if resp.status_code in (401, 403):
                    raise RuntimeError(
                        "ChatGPT recusou o acesso — reconecte em Conexões → Assinaturas. "
                        f"(HTTP {resp.status_code}: {body})"
                    )
                raise RuntimeError(f"Codex HTTP {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except json.JSONDecodeError:
                    continue
                et = ev.get("type") or ""
                if et == "response.output_text.delta":
                    yield {"choices": [{"delta": {"content": ev.get("delta") or ""}}]}
                elif et == "response.reasoning_summary_text.delta":
                    yield {"choices": [{"delta": {"reasoning": ev.get("delta") or ""}}]}
                elif et == "response.output_item.done":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        yield {"choices": [{"delta": {"tool_calls": [{
                            "index": tool_idx,
                            "id": item.get("call_id") or item.get("id") or f"call_{tool_idx}",
                            "type": "function",
                            "function": {
                                "name": item.get("name") or "",
                                "arguments": item.get("arguments") or "{}",
                            },
                        }]}}]}
                        tool_idx += 1
                elif et == "response.completed":
                    r = ev.get("response") or {}
                    u = r.get("usage") or {}
                    finish = "tool_calls" if tool_idx else "stop"
                    yield {
                        "choices": [{"delta": {}, "finish_reason": finish}],
                        # assinatura: sem custo por token (cost fica 0 no ledger)
                        "usage": {
                            "prompt_tokens": u.get("input_tokens") or 0,
                            "completion_tokens": u.get("output_tokens") or 0,
                            "total_tokens": u.get("total_tokens") or 0,
                            "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                            "cached_tokens": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
                        },
                    }
                elif et in ("response.failed", "error"):
                    msg = ((ev.get("response") or {}).get("error") or {}).get("message") \
                        or ev.get("message") or "erro do backend do Codex"
                    raise RuntimeError(f"Codex: {str(msg)[:300]}")


async def complete(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 120.0,  # noqa: ARG001 - interface comum; o stream tem timeout próprio
) -> str:
    """Não-streaming (títulos, roteadores): consome o stream e junta o texto."""
    parts: list[str] = []
    async for chunk in stream_chat(api_key, model, messages, params=params):
        for c in chunk.get("choices") or []:
            d = (c.get("delta") or {}).get("content")
            if d:
                parts.append(d)
    return "".join(parts)
