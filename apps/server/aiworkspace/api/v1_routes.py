"""Endpoints compatíveis com a API da OpenAI.

`POST /v1/chat/completions`, `GET /v1/models` e `GET /v1/models/{id}` respondem no
formato que os SDKs oficiais (openai-python, openai-node, LangChain, n8n…) já sabem
ler: basta apontar `base_url` para cá e usar a chave `aw-…`.

Campos extras da plataforma ficam num objeto `aiworkspace` dentro da resposta —
fora dos campos padrão, para não confundir um cliente estrito.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..db import SessionLocal
from . import jobs, keys_service, limits, runner, webhooks
from .deps import ApiContext, ApiError, enforce_quotas, require_api_key, scoped

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["api"])


def _now() -> int:
    return int(time.time())


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #

def _model_card(mc, allowed: bool) -> dict[str, Any]:
    caps = mc.capabilities or {}
    return {
        "id": runner.model_public_id(mc),
        "object": "model",
        "created": int(mc.created_at.timestamp()) if mc.created_at else _now(),
        "owned_by": "aiworkspace",
        # metadados que a OpenAI não tem, mas que definem o que este modelo faz
        "aiworkspace": {
            "name": mc.name,
            "description": mc.description or "",
            "base_model": mc.base_model,
            "allowed": allowed,
            "capabilities": {
                "tools": bool(mc.tools_enabled),
                "vision": bool(caps.get("vision")),
                "knowledge": bool((caps.get("knowledge") or {}).get("bases")),
                "memory": (caps.get("memory") or {}).get("enabled") is not False,
                "artifacts": bool(caps.get("artifacts")),
                "image_output": bool(caps.get("image_output")),
            },
        },
    }


@router.get("/models")
async def list_models(ctx: ApiContext = Depends(scoped("models:read"))):
    rows = await runner.list_api_models(ctx.db, ctx.user)
    data = [
        _model_card(mc, keys_service.model_allowed(ctx.key, runner.model_public_id(mc)))
        for mc in rows
    ]
    # a chave que restringe modelos só enxerga os dela — listar o que ela não pode
    # usar seria vazar a configuração da conta para a aplicação integrada
    if (ctx.key.model_policy or {}).get("mode") == "allow":
        data = [d for d in data if d["aiworkspace"]["allowed"]]
    return {"object": "list", "data": data}


@router.get("/models/{model_id}")
async def get_model(model_id: str, ctx: ApiContext = Depends(scoped("models:read"))):
    for mc in await runner.list_api_models(ctx.db, ctx.user):
        if model_id in (mc.slug or "", str(mc.id)):
            allowed = keys_service.model_allowed(ctx.key, runner.model_public_id(mc))
            if not allowed and (ctx.key.model_policy or {}).get("mode") == "allow":
                raise ApiError(f"O modelo '{model_id}' não está liberado para esta chave.",
                               status=403, type_="permission_error",
                               code="model_not_allowed")
            return _model_card(mc, allowed)
    raise ApiError(f"Modelo '{model_id}' não encontrado.", status=404,
                   code="model_not_found")


# --------------------------------------------------------------------------- #
# Chat completions
# --------------------------------------------------------------------------- #

def _chunk(cid: str, model: str, delta: dict[str, Any],
           finish: str | None = None) -> str:
    payload = {
        "id": cid, "object": "chat.completion.chunk", "created": _now(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _envelope(cid: str, model: str, collected: dict[str, Any], mode: str) -> dict[str, Any]:
    usage = collected.get("usage") or {}
    out: dict[str, Any] = {
        "id": cid,
        "object": "chat.completion",
        "created": _now(),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": collected.get("content") or ""},
            "finish_reason": collected.get("finish_reason") or "stop",
        }],
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
        "aiworkspace": {
            "mode": mode,
            "cost": round(float(usage.get("cost") or 0.0), 6),
            "tools": collected.get("tools") or [],
            "memories": collected.get("memories") or [],
        },
    }
    if collected.get("reasoning"):
        out["aiworkspace"]["reasoning"] = collected["reasoning"]
    return out


async def _collect_platform(ctx: ApiContext, rm, parsed, body) -> dict[str, Any]:
    """Roda o turno inteiro e devolve o resultado consolidado (modo não-streaming)."""
    collected: dict[str, Any] = {"content": "", "usage": None, "tools": []}
    async for ev in runner.run_platform_turn(ctx, rm, parsed, body):
        kind = ev.get("type")
        if kind == "done":
            collected["content"] = ev.get("content") or ""
            collected["usage"] = ev.get("usage")
            collected["reasoning"] = ev.get("reasoning")
            collected["memories"] = ev.get("memories")
            # tool_events traz a CHAMADA e o RESULTADO de cada ferramenta; contar as
            # duas duplicaria a lista ("run_code, run_code") como se tivesse rodado
            # duas vezes
            for t in ev.get("tool_events") or []:
                if isinstance(t, dict) and t.get("type") == "tool_call" and t.get("name"):
                    collected["tools"].append(t["name"])
        elif kind == "error":
            raise ApiError(ev.get("message") or "Falha ao gerar a resposta.",
                           status=502, type_="api_error", code="upstream_error")
    return collected


async def _stream_platform(ctx: ApiContext, rm, parsed, body, cid: str, started: float):
    """SSE no formato OpenAI. Os eventos de ferramenta viram chunks anotados no
    objeto `aiworkspace` — um cliente estrito ignora o campo extra e continua lendo
    `choices[0].delta.content` normalmente.

    Abre a PRÓPRIA sessão de banco: este gerador só começa a rodar depois que a
    resposta já saiu, e a sessão da dependency (`get_db`) fecha nesse momento —
    usá-la aqui daria "session is closed" no meio do stream.
    """
    model = rm.public_id
    collected: dict[str, Any] = {"content": "", "usage": None, "tools": []}
    status, error = 200, ""
    yield _chunk(cid, model, {"role": "assistant", "content": ""})
    async with SessionLocal() as db:
        ctx.db = db
        try:
            try:
                async for ev in runner.run_platform_turn(ctx, rm, parsed, body):
                    kind = ev.get("type")
                    if kind == "token":
                        text = ev.get("text") or ""
                        collected["content"] += text
                        yield _chunk(cid, model, {"content": text})
                    elif kind == "reasoning" and body.get("include_reasoning"):
                        yield _chunk(cid, model, {"reasoning": ev.get("text") or ""})
                    elif kind in ("tool_call", "tool_result"):
                        name = ev.get("name") or ""
                        if kind == "tool_call" and name:
                            collected["tools"].append(name)
                        payload = {
                            "id": cid, "object": "chat.completion.chunk", "created": _now(),
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                            "aiworkspace": {"event": kind, "name": name},
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    elif kind == "done":
                        collected["content"] = ev.get("content") or collected["content"]
                        collected["usage"] = ev.get("usage")
                        collected["reasoning"] = ev.get("reasoning")
                    elif kind == "error":
                        status, error = 502, str(ev.get("message") or "")[:255]
                        yield f"data: {json.dumps({'error': {'message': error, 'type': 'api_error'}})}\n\n"
            except Exception as exc:  # noqa: BLE001 - o stream já começou; erro vai no corpo
                status, error = 500, str(exc)[:255]
                logger.exception("erro no stream da API")
                yield f"data: {json.dumps({'error': {'message': error, 'type': 'api_error'}})}\n\n"
            else:
                yield _chunk(cid, model, {}, finish="stop")
                usage = collected.get("usage") or {}
                yield (
                    "data: "
                    + json.dumps({
                        "id": cid, "object": "chat.completion.chunk", "created": _now(),
                        "model": model, "choices": [],
                        "usage": {
                            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                            "completion_tokens": int(usage.get("completion_tokens") or 0),
                            "total_tokens": int(usage.get("total_tokens") or 0),
                        },
                        "aiworkspace": {
                            "mode": "platform",
                            "cost": round(float(usage.get("cost") or 0.0), 6),
                            "tools": collected["tools"],
                        },
                    }, ensure_ascii=False)
                    + "\n\n"
                )
            # sentinela do protocolo: só faz sentido com o cliente ainda lendo, e
            # fica FORA do finally de propósito (ver _finish_stream).
            yield "data: [DONE]\n\n"
        finally:
            rec = (runner.usage_record_for(rm, collected["usage"])
                   if collected.get("usage") else None)
            await _finish_stream(
                db, ctx, endpoint="chat.completions", model=model, started=started,
                status=status, error=error, usage=collected.get("usage"), rec=rec,
            )


async def _finish_stream(
    db, ctx: ApiContext, *, endpoint: str, model: str, started: float,
    status: int, error: str, usage: dict[str, Any] | None, rec: Any = None,
) -> None:
    """Encerramento de um stream: devolve a vaga de concorrência e registra a
    chamada. Roda no `finally` do gerador, que TAMBÉM executa quando o cliente
    desconecta no meio (o Starlette faz `aclose()`, lançando GeneratorExit).

    Por isso este caminho NUNCA pode dar `yield`: um `yield` durante o
    GeneratorExit vira `RuntimeError: async generator ignored GeneratorExit` e
    aborta o resto do bloco — era o bug que deixava a vaga presa (a chave travava
    em 429 até reiniciar) e, pior, pulava o `record`, então a chamada não entrava
    em `api_requests` e não contava para RPD/mensal/tokens/orçamento: quem
    desconectasse no meio do stream usava o modelo de graça, fora das cotas.

    Também não deixa exceção escapar: falhar ao registrar não pode mascarar o
    erro original nem impedir a liberação da vaga (que vem primeiro).
    """
    limits.release_slot(ctx.key)
    try:
        await runner.record(db, ctx, endpoint=endpoint, model=model, status=status,
                            error=error, started=started, usage=usage, rec=rec)
        if status >= 400:
            webhooks.emit(ctx.key, "request.error", {"status": status, "message": error})
    except Exception:  # noqa: BLE001 - contabilidade não derruba o encerramento
        logger.exception("falha ao registrar a chamada da API ao encerrar o stream")


async def _stream_passthrough(ctx: ApiContext, rm, messages, body, started: float):
    """Encaminha os chunks do provedor sem tocar — é o modo de function calling
    do cliente, onde qualquer normalização nossa atrapalharia."""
    usage: dict[str, Any] = {}
    status, error = 200, ""
    try:
        try:
            async for chunk in runner.run_passthrough(rm, messages, body):
                if chunk.get("usage"):
                    usage = chunk["usage"]
                chunk["model"] = rm.public_id
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            status, error = 502, str(exc)[:255]
            yield f"data: {json.dumps({'error': {'message': error, 'type': 'api_error'}})}\n\n"
        # fora do finally de propósito (ver _finish_stream)
        yield "data: [DONE]\n\n"
    finally:
        async with SessionLocal() as db:
            ctx.db = db
            await _finish_stream(db, ctx, endpoint="chat.completions",
                                 model=rm.public_id, started=started, status=status,
                                 error=error, usage=usage)


@router.post("/chat/completions")
async def chat_completions(request: Request, ctx: ApiContext = Depends(scoped("chat"))):
    started = time.perf_counter()
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise ApiError("Corpo inválido: esperado JSON.", code="invalid_json") from None
    if not isinstance(body, dict):
        raise ApiError("Corpo inválido: esperado um objeto JSON.", code="invalid_json")

    await enforce_quotas(ctx)
    rm = await runner.resolve_model(ctx.db, ctx, str(body.get("model") or ""))

    if not limits.acquire_slot(ctx.key):
        raise ApiError(
            f"Limite de {(ctx.key.limits or {}).get('concurrency')} requisições "
            "simultâneas atingido para esta chave.",
            status=429, type_="rate_limit_error", code="concurrency_limit",
            headers={"Retry-After": "1"},
        )

    # Posse da vaga de concorrência: os caminhos que continuam DEPOIS desta função
    # (geradores de streaming, job de background) assumem a posse e liberam no
    # próprio `finally`; nos caminhos síncronos a posse fica com a rota, que libera
    # uma única vez no `finally` abaixo. Sem essa distinção, liberar aqui E lá
    # decrementava a vaga de OUTRA requisição em voo (a chave passava do limite).
    slot_handed_off = False
    try:
        client_tools = body.get("tools")
        cid = _completion_id()

        if client_tools:
            # function calling do cliente: encaminhamos as mensagens como vieram
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ApiError("O campo 'messages' é obrigatório.", code="messages_required")
            if body.get("stream"):
                resp = StreamingResponse(
                    _stream_passthrough(ctx, rm, messages, body, started),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
                slot_handed_off = True
                return resp
            return await _passthrough_sync(ctx, rm, messages, body, cid, started)

        parsed = runner.parse_messages(body.get("messages"))

        if body.get("background"):
            resp = await _start_background(ctx, rm, parsed, body, started)
            slot_handed_off = True  # só depois de o job existir de fato
            return resp

        if body.get("stream"):
            resp = StreamingResponse(
                _stream_platform(ctx, rm, parsed, body, cid, started),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
            slot_handed_off = True
            return resp

        collected = await _collect_platform(ctx, rm, parsed, body)
    except ApiError as exc:
        await runner.record(ctx.db, ctx, endpoint="chat.completions",
                            model=rm.public_id, status=exc.status,
                            error=exc.message, started=started)
        webhooks.emit(ctx.key, "request.error", {"status": exc.status, "message": exc.message})
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("erro na completion da API")
        await runner.record(ctx.db, ctx, endpoint="chat.completions",
                            model=rm.public_id, status=500, error=str(exc),
                            started=started)
        raise ApiError("Erro interno ao gerar a resposta.", status=500,
                       type_="api_error", code="internal_error") from None
    finally:
        if not slot_handed_off:
            limits.release_slot(ctx.key)

    rec = runner.usage_record_for(rm, collected.get("usage")) if collected.get("usage") else None
    await runner.record(ctx.db, ctx, endpoint="chat.completions", model=rm.public_id,
                        started=started, usage=collected.get("usage"), rec=rec)
    return _envelope(cid, rm.public_id, collected, "platform")


async def _passthrough_sync(ctx: ApiContext, rm, messages, body, cid: str, started: float):
    """Passthrough sem streaming: consome os chunks e remonta a resposta completa,
    inclusive `tool_calls` (é justamente o que o cliente veio buscar)."""
    content = ""
    tool_calls: dict[int, dict[str, Any]] = {}
    finish = "stop"
    usage: dict[str, Any] = {}
    async for chunk in runner.run_passthrough(rm, messages, body):
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content += delta.get("content") or ""
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index") or 0)
                slot = tool_calls.setdefault(
                    idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]

    # a vaga NÃO é liberada aqui: a posse é da rota (chat_completions), que libera
    # no `finally` dela. Liberar nos dois lugares derrubava a vaga de outra requisição.
    await runner.record(ctx.db, ctx, endpoint="chat.completions", model=rm.public_id,
                        started=started, usage=usage)
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        finish = "tool_calls"
    return {
        "id": cid, "object": "chat.completion", "created": _now(), "model": rm.public_id,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
        "aiworkspace": {"mode": "passthrough"},
    }


# --------------------------------------------------------------------------- #
# Chamada assíncrona
# --------------------------------------------------------------------------- #

async def _start_background(ctx: ApiContext, rm, parsed, body, started: float):
    """Dispara o turno numa task e responde na hora com o id do job."""
    job_id = await jobs.create(str(ctx.user.id), str(ctx.key.id), rm.public_id)
    key, user, ip = ctx.key, ctx.user, ctx.ip

    async def _work() -> None:
        await jobs.update(job_id, status="running")
        async with SessionLocal() as db:
            bg = ApiContext(user=user, key=key, db=db, ip=ip)
            try:
                collected = await _collect_platform(bg, rm, parsed, body)
            except Exception as exc:  # noqa: BLE001
                message = getattr(exc, "message", None) or str(exc)
                await jobs.update(job_id, status="failed", error=message[:500])
                await runner.record(db, bg, endpoint="chat.completions.async",
                                    model=rm.public_id, status=502, error=message,
                                    started=started)
                webhooks.emit(key, "request.error", {"status": 502, "message": message[:255]})
                return
            finally:
                limits.release_slot(key)
            result = _envelope(_completion_id(), rm.public_id, collected, "platform")
            await jobs.update(job_id, status="completed", result=result)
            rec = runner.usage_record_for(rm, collected.get("usage")) if collected.get("usage") else None
            await runner.record(db, bg, endpoint="chat.completions.async",
                                model=rm.public_id, started=started,
                                usage=collected.get("usage"), rec=rec)

    asyncio.create_task(_work())
    return JSONResponse(
        status_code=202,
        content={"id": job_id, "object": "chat.completion.job", "status": "queued",
                 "model": rm.public_id,
                 "poll_url": f"/v1/chat/completions/{job_id}"},
    )


@router.get("/chat/completions/{job_id}")
async def get_job(job_id: str, ctx: ApiContext = Depends(scoped("chat"))):
    job = await jobs.get(job_id, str(ctx.user.id))
    if job is None:
        raise ApiError(
            "Job não encontrado ou expirado (resultados ficam disponíveis por 1 hora).",
            status=404, code="job_not_found",
        )
    if job["status"] == "completed":
        return {"id": job_id, "object": "chat.completion.job", "status": "completed",
                "response": job["result"]}
    return {"id": job_id, "object": "chat.completion.job", "status": job["status"],
            "error": job.get("error")}
