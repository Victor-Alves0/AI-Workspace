"""Aba Ferramentas: CRUD de tools criadas pelo usuário + execução de teste.

A criação/edição é restrita a admin, pois executa código Python no servidor.
Mudanças invalidam o cache da instância SIFT do usuário.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth.deps import require_approved, require_admin
from .db import get_db
from .models import Tool, User
from .tools import sift_service

router = APIRouter(prefix="/tools", tags=["tools"])

_TEMPLATE = '''def run(**params):
    """Sua ferramenta. Receba parâmetros e retorne algo serializável em JSON."""
    return {"echo": params}
'''


def _clean_tags(tags: list[str]) -> list[str]:
    """Normaliza tags: minúsculas, sem duplicatas, no máximo 10 de 40 chars."""
    out: list[str] = []
    for t in tags:
        t = t.strip().lower()[:40]
        if t and t not in out:
            out.append(t)
    return out[:10]


class ToolIn(BaseModel):
    path: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$", max_length=255)
    name: str = Field(max_length=255)
    description: str = Field(default="", max_length=2000)
    params: dict[str, str] = Field(default_factory=dict)
    returns: list[str] = Field(default_factory=list)
    code: str = Field(default=_TEMPLATE, max_length=50_000)
    valves: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    tool_type: str = Field(default="code", pattern=r"^(code|mcp)$")
    mcp_config: dict[str, Any] = Field(default_factory=dict)


class ToolUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    params: dict[str, str] | None = None
    returns: list[str] | None = None
    code: str | None = Field(default=None, max_length=50_000)
    valves: dict[str, Any] | None = None
    enabled: bool | None = None
    tags: list[str] | None = None
    tool_type: str | None = Field(default=None, pattern=r"^(code|mcp)$")
    mcp_config: dict[str, Any] | None = None


class ToolTestIn(BaseModel):
    code: str = Field(max_length=50_000)
    params: dict[str, Any] = Field(default_factory=dict)
    valves: dict[str, Any] = Field(default_factory=dict)


class ValvesSchemaIn(BaseModel):
    code: str = Field(max_length=50_000)


class ToolOut(BaseModel):
    id: uuid.UUID
    path: str
    name: str
    description: str
    params: dict[str, str]
    returns: list[str]
    code: str
    valves: dict[str, Any]
    enabled: bool
    tags: list[str]
    tool_type: str
    mcp_config: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/system")
async def list_system_tools(user: User = Depends(require_approved)):
    """Ferramentas de sistema (embutidas) disponíveis para vincular a um modelo."""
    return sift_service.system_tools()


@router.get("", response_model=list[ToolOut])
async def list_tools(user: User = Depends(require_approved), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Tool).where(Tool.user_id == user.id).order_by(Tool.path)
    )
    return list(rows)


@router.post("", response_model=ToolOut)
async def create_tool(
    body: ToolIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    data = body.model_dump()
    data["tags"] = _clean_tags(data["tags"])
    tool = Tool(user_id=user.id, **data)
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    sift_service.invalidate(str(user.id))
    return tool


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: uuid.UUID,
    body: ToolUpdate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tool = await db.get(Tool, tool_id)
    if tool is None or tool.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ferramenta não encontrada")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "tags" and value is not None:
            value = _clean_tags(value)
        setattr(tool, field, value)
    await db.commit()
    await db.refresh(tool)
    sift_service.invalidate(str(user.id))
    return tool


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: uuid.UUID,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tool = await db.get(Tool, tool_id)
    if tool is None or tool.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ferramenta não encontrada")
    await db.delete(tool)
    await db.commit()
    sift_service.invalidate(str(user.id))
    return {"ok": True}


@router.post("/test")
async def test_tool(body: ToolTestIn, user: User = Depends(require_admin)):
    """Executa o código com os parâmetros informados e devolve o resultado/erro."""
    try:
        result = await run_in_threadpool(
            sift_service.run_user_code, body.code, body.params, body.valves
        )
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@router.post("/valves-schema")
async def valves_schema(body: ValvesSchemaIn, user: User = Depends(require_admin)):
    """Retorna os defaults das valves (dict VALVES) declaradas no código."""
    defaults = await run_in_threadpool(sift_service.valves_defaults, body.code)
    return {"defaults": defaults}


# --------------------------------------------------------------------------- #
# Integração MCP: teste de conexão (cliente JSON-RPC mínimo)
# --------------------------------------------------------------------------- #
class McpTestIn(BaseModel):
    url: str = Field(max_length=1000)
    transport: str = Field(default="http", pattern=r"^(http|sse)$")
    headers: dict[str, str] = Field(default_factory=dict)


async def _mcp_post(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    """POST JSON-RPC; servidores streamable HTTP podem responder como SSE."""
    async with client.stream("POST", url, json=payload, headers=headers) as resp:
        resp.raise_for_status()
        if "text/event-stream" in resp.headers.get("content-type", ""):
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                        return msg, resp.headers
            raise ValueError("stream SSE terminou sem resposta JSON-RPC")
        body = await resp.aread()
        return json.loads(body), resp.headers


@router.post("/mcp/test")
async def mcp_test(body: McpTestIn, user: User = Depends(require_admin)):
    """Testa a conexão com um servidor MCP: initialize + tools/list.

    Retorna sempre 200 com {ok, ...} — o resultado (incl. falha) é exibido na UI.
    """
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL deve começar com http:// ou https://"}
    extra = {str(k)[:100]: str(v)[:1000] for k, v in list((body.headers or {}).items())[:20]}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
            if body.transport == "sse":
                # transporte SSE legado: valida que o stream abre e emite eventos
                async with client.stream(
                    "GET", url, headers={**extra, "Accept": "text/event-stream"}
                ) as resp:
                    resp.raise_for_status()
                    if "text/event-stream" not in resp.headers.get("content-type", ""):
                        return {"ok": False, "error": "o servidor não respondeu como SSE"}
                    async for line in resp.aiter_lines():
                        if line.strip():
                            return {"ok": True, "transport": "sse", "detail": "stream SSE aberto e emitindo eventos"}
                    return {"ok": False, "error": "stream SSE fechou sem eventos"}

            # streamable HTTP: initialize -> notifications/initialized -> tools/list
            headers = {**extra, "Accept": "application/json, text/event-stream"}
            init = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "Singularity AI", "version": "0.1.0"},
                },
            }
            msg, rheaders = await _mcp_post(client, url, headers, init)
            if "error" in msg:
                err = msg["error"]
                detail = err.get("message", err) if isinstance(err, dict) else err
                return {"ok": False, "error": f"initialize falhou: {detail}"}
            info = (msg.get("result") or {}).get("serverInfo") or {}
            sid = rheaders.get("mcp-session-id")
            if sid:
                headers["Mcp-Session-Id"] = sid
            try:  # alguns servidores exigem a notificação antes de tools/list
                await client.post(
                    url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers
                )
            except httpx.HTTPError:
                pass
            tools: list[str] = []
            try:
                msg2, _ = await _mcp_post(
                    client, url, headers,
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )
                tools = [t.get("name", "?") for t in (msg2.get("result") or {}).get("tools", [])]
            except Exception:  # noqa: BLE001 - initialize já provou a conexão
                pass
            return {
                "ok": True,
                "server": {"name": info.get("name"), "version": info.get("version")},
                "tools_count": len(tools),
                "tools": tools[:15],
            }
    except httpx.ConnectError:
        return {"ok": False, "error": "não foi possível conectar ao servidor"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "tempo esgotado (10s)"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"HTTP {exc.response.status_code} do servidor"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
