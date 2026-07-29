"""Cliente OpenRouter (API compatível com OpenAI): lista modelos e faz chat streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# Escada de esforço de raciocínio, do MAIOR ao MENOR. Oferecemos a escada completa na
# UI (o OpenRouter não diz quais níveis cada modelo aceita); se o provider recusar o
# nível pedido (ex.: "xhigh" num modelo que só vai até "high"), o fallback automático
# desce um degrau e refaz — avisando a UI p/ o seletor refletir o que funcionou.
_EFFORT_LADDER = ["xhigh", "high", "medium", "low", "minimal"]


def _lower_effort(payload: dict[str, Any], body: str) -> str | None:
    """Se o erro do provider é de nível de raciocínio E há um degrau abaixo, rebaixa o
    esforço no ``payload`` (in-place) e devolve o novo nível. No menor nível, remove o
    raciocínio e devolve ``"off"``. Devolve ``None`` quando não é caso de rebaixar
    (erro não relacionado, ou sem esforço explícito no payload)."""
    low = body.lower()
    if not any(k in low for k in ("effort", "reasoning", "xhigh", "minimal", "verbosity")):
        return None
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") in _EFFORT_LADDER:
        cur, where = reasoning["effort"], "obj"
    elif payload.get("reasoning_effort") in _EFFORT_LADDER:
        cur, where = payload["reasoning_effort"], "flat"
    else:
        return None
    idx = _EFFORT_LADDER.index(cur)
    if idx + 1 < len(_EFFORT_LADDER):
        nxt = _EFFORT_LADDER[idx + 1]
        if where == "obj":
            payload["reasoning"] = {**reasoning, "effort": nxt}
        else:
            payload["reasoning_effort"] = nxt
        return nxt
    # já no menor degrau: desliga o raciocínio de vez e sinaliza "off"
    payload.pop("reasoning", None)
    payload.pop("reasoning_effort", None)
    return "off"


def _headers(api_key: str) -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # identifica o app no dashboard do OpenRouter (nome + URL, não o localhost)
        "HTTP-Referer": settings.openrouter_app_url,
        "X-Title": settings.openrouter_app_name,
    }


# Cache do catálogo de modelos. O /models do OpenRouter é o MESMO para todo mundo
# (é o catálogo público; a chave só autentica) e muda raramente — mas o seletor de
# modelo o buscava a CADA abertura, pagando ~300ms de rede sempre. TTL de 10min:
# um modelo novo aparece em minutos, e as aberturas seguintes são instantâneas.
_CATALOG_TTL = 600.0
_catalog_cache: tuple[float, list[dict[str, Any]]] | None = None
_catalog_lock = asyncio.Lock()


async def list_models(api_key: str, *, force: bool = False) -> list[dict[str, Any]]:
    global _catalog_cache
    now = time.monotonic()
    if not force and _catalog_cache is not None and now - _catalog_cache[0] < _CATALOG_TTL:
        return _catalog_cache[1]
    # lock evita "stampede": N aberturas simultâneas do seletor fariam N fetches;
    # o 1º busca, os outros reusam o resultado recém-cacheado.
    async with _catalog_lock:
        if not force and _catalog_cache is not None and time.monotonic() - _catalog_cache[0] < _CATALOG_TTL:
            return _catalog_cache[1]
        settings = get_settings()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url}/models", headers=_headers(api_key)
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
        _catalog_cache = (time.monotonic(), data)
        return data


def invalidate_catalog() -> None:
    """Descarta o catálogo cacheado (ex.: após trocar a chave do OpenRouter)."""
    global _catalog_cache
    _catalog_cache = None


def _compat_model(model: str, base_url: str | None) -> str:
    """Modelo enviado à API: provedores OpenAI-compatíveis (base_url setado) recebem o
    id SEM o prefixo interno de roteamento. Ollama = `ollama/<nome>`; provedores
    customizados = `@<slug>/<id>` (o id real pode conter `/`, ex.: `@kie/google/veo-3`
    → `google/veo-3`). O OpenRouter (base_url=None) recebe o id como está."""
    if not base_url:
        return model
    if model.startswith("@"):
        parts = model.split("/", 1)
        return parts[1] if len(parts) == 2 else model
    from ..integrations.ollama_service import MODEL_PREFIX
    if model.startswith(MODEL_PREFIX):
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
    `base_url` roteia p/ um provedor OpenAI-compatível (Ollama); None = OpenRouter.
    Modelos `codex/*` (assinatura ChatGPT) delegam ao adaptador de protocolo —
    TODOS os call sites (títulos, roteadores, juízes) funcionam sem saber disso."""
    from . import chatgpt_codex
    if chatgpt_codex.is_codex_model(model):
        return await chatgpt_codex.complete(api_key, model, messages, params=params, timeout=timeout)
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

    from . import chatgpt_codex
    if chatgpt_codex.is_codex_model(model):
        t0 = time.monotonic()
        try:
            text = await chatgpt_codex.complete(api_key, model, messages, params=params, timeout=timeout)
            return {"text": text, "prompt_tokens": None, "completion_tokens": None,
                    "cost": None, "latency_ms": int((time.monotonic() - t0) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "latency_ms": int((time.monotonic() - t0) * 1000)}
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
    reasoning) que o servidor local pode rejeitar. Modelos `codex/*` (assinatura
    ChatGPT) delegam ao adaptador de protocolo (Responses API → mesmos chunks).
    """
    from . import chatgpt_codex
    if chatgpt_codex.is_codex_model(model):
        async for chunk in chatgpt_codex.stream_chat(api_key, model, messages, tools=tools, params=params):
            yield chunk
        return
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
    url = f"{(base_url or settings.openrouter_base_url)}/chat/completions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        # loop de fallback do esforço de raciocínio: se o provider recusar o nível
        # (erro ANTES do 1º token), desce um degrau e refaz. Cada rebaixamento emite
        # {"type":"reasoning_effort"} p/ a UI atualizar o seletor. Sai no 1º stream ok.
        while True:
            async with client.stream("POST", url, headers=_headers(api_key), json=payload) as resp:
                if resp.status_code >= 400:
                    # lê o corpo ANTES de levantar: raise_for_status num stream só dá
                    # o código HTTP; o motivo real (ex.: "model does not support image
                    # output" / "unsupported reasoning effort") está no JSON de erro.
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    lowered = _lower_effort(payload, body)
                    if lowered is not None:
                        logger.info("Nível de raciocínio recusado pelo provider; caindo para '%s'", lowered)
                        yield {"type": "reasoning_effort", "effort": lowered}
                        continue  # refaz o request com o esforço rebaixado
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
            return
