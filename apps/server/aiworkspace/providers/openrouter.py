"""Cliente OpenRouter (API compatível com OpenAI): lista modelos e faz chat streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from ..config import get_settings


def _headers(api_key: str) -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # identifica o app no dashboard do OpenRouter (nome + URL, não o localhost)
        "HTTP-Referer": settings.openrouter_app_url,
        "X-Title": settings.openrouter_app_name,
    }


async def list_models(api_key: str) -> list[dict[str, Any]]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{settings.openrouter_base_url}/models", headers=_headers(api_key)
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


def _compat_model(model: str, base_url: str | None) -> str:
    """Modelo enviado à API: para provedores OpenAI-compatíveis (base_url setado, ex.:
    Ollama), remove o prefixo `ollama/` — o servidor local conhece só o nome puro."""
    from ..integrations.ollama_service import MODEL_PREFIX
    if base_url and model.startswith(MODEL_PREFIX):
        return model[len(MODEL_PREFIX):]
    return model


async def complete(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 120.0,
    base_url: str | None = None,
) -> str:
    """Completion NÃO-streaming; devolve só o texto da resposta.

    Usada em etapas de pré-processamento (ex.: Vision Router descrevendo imagens),
    onde não faz sentido streamar ao usuário — queremos só o texto resultante.
    `base_url` roteia p/ um provedor OpenAI-compatível (Ollama); None = OpenRouter."""
    settings = get_settings()
    _RESERVED = {"model", "messages", "stream", "stream_options", "tools", "tool_choice"}
    safe_params = {k: v for k, v in (params or {}).items() if k not in _RESERVED}
    if base_url:
        safe_params.pop("reasoning", None)  # não é padrão OpenAI; Ollama pode rejeitar
    payload: dict[str, Any] = {"model": _compat_model(model, base_url), "messages": messages, "stream": False, **safe_params}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{(base_url or settings.openrouter_base_url)}/chat/completions",
            headers=_headers(api_key),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


async def complete_verbose(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 120.0,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Completion NÃO-streaming que devolve texto + métricas (tokens, custo, latência).

    Usada pelo Playground (benchmarks/comparações) onde o que importa é medir cada
    resposta. Nunca levanta p/ fora: em erro devolve {"error": ...}. `cost` só existe
    no OpenRouter (extensão `usage.include`); em compat (Ollama) fica None."""
    import time

    settings = get_settings()
    _RESERVED = {"model", "messages", "stream", "stream_options", "usage", "tools", "tool_choice"}
    safe_params = {k: v for k, v in (params or {}).items() if k not in _RESERVED}
    if base_url:
        safe_params.pop("reasoning", None)  # não é padrão OpenAI; Ollama pode rejeitar
    payload: dict[str, Any] = {
        "model": _compat_model(model, base_url),
        "messages": messages,
        "stream": False,
        **safe_params,
    }
    if not base_url:
        payload["usage"] = {"include": True}  # custo (extensão do OpenRouter)
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{(base_url or settings.openrouter_base_url)}/chat/completions",
                headers=_headers(api_key),
                json=payload,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            body = resp.text[:300]
            return {"error": f"HTTP {resp.status_code}: {body}", "latency_ms": latency_ms}
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "latency_ms": int((time.monotonic() - t0) * 1000)}
    choices = data.get("choices") or []
    text = "" if not choices else ((choices[0].get("message") or {}).get("content") or "")
    usage = data.get("usage") or {}
    return {
        "text": text,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost": usage.get("cost"),
        "latency_ms": latency_ms,
    }


async def stream_chat(
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict] | None = None,
    params: dict[str, Any] | None = None,
    modalities: list[str] | None = None,
    base_url: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Faz streaming de uma completion e gera cada objeto `chunk` (delta OpenAI).

    O chamador (orchestrator) decide o que fazer com deltas de texto vs tool_calls.
    `base_url` roteia p/ um provedor OpenAI-compatível (Ollama = modelos locais);
    None = OpenRouter. Em compat, omitimos campos só-OpenRouter (usage/modalities/
    reasoning) que o servidor local pode rejeitar.
    """
    settings = get_settings()
    compat = bool(base_url)
    # `params` vem da config do chat (controlada pelo usuário). São parâmetros de
    # GERAÇÃO (temperature, top_p, max_tokens, reasoning…). Removemos chaves
    # estruturais para que o usuário não redefina messages/model/stream via params
    # (o histórico e o modelo são resolvidos pelo servidor).
    _RESERVED = {"model", "messages", "stream", "stream_options", "usage", "tools", "tool_choice"}
    safe_params = {k: v for k, v in (params or {}).items() if k not in _RESERVED}
    if compat:
        safe_params.pop("reasoning", None)  # não é padrão OpenAI
    payload: dict[str, Any] = {
        "model": _compat_model(model, base_url),
        "messages": messages,
        "stream": True,
        # pede a contabilização de tokens no stream (padrão OpenAI)
        "stream_options": {"include_usage": True},
        **safe_params,
    }
    if not compat:
        payload["usage"] = {"include": True}  # custo (extensão do OpenRouter)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if modalities and not compat:  # ex.: ["image","text"] p/ modelos que geram imagem
        payload["modalities"] = modalities

    # read=300: o timeout é POR LEITURA (entre chunks), não do stream inteiro — o
    # OpenRouter manda keepalives durante processamento longo, então 5min sem NENHUM
    # byte = conexão morta. Sem isso (read=None), um TCP quebrado deixava o driver da
    # geração pendurado p/ sempre e o chat preso em "Pensando…" sem erro nem fim.
    timeout = httpx.Timeout(connect=15.0, write=30.0, read=300.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{(base_url or settings.openrouter_base_url)}/chat/completions",
            headers=_headers(api_key),
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                # lê o corpo ANTES de levantar: raise_for_status num stream só dá o
                # código HTTP; o motivo real (ex.: "model does not support image
                # output") está no JSON de erro do OpenRouter.
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
