"""Grafo de Investigação: leitura pela UI (lista/visualiza/apaga).

A ESCRITA é feita pela IA via a tool `investigation.graph` (investigation_service);
estes endpoints só servem a UI que renderiza o grafo no canvas. Espelha o
`graph/visualize` do Codespace, mas o grafo aqui é preenchido pela IA investigando,
não derivado de código-fonte.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from .auth.deps import require_approved
from .models import User
from . import investigation_service

# Sem dependência de `db`: o investigation_service abre uma engine EFÊMERA própria
# (NullPool) — o mesmo caminho usado pela tool (threadpool + asyncio.run) —, então
# injetar a AsyncSession da requisição aqui só abriria uma conexão do pool sem uso.
router = APIRouter(prefix="/investigation", tags=["investigation"])


@router.get("/graphs")
async def list_graphs(
    chat_id: str = "",
    user: User = Depends(require_approved),
):
    """Grafos do usuário (opcionalmente só os deste chat), com contagem de nós/arestas."""
    return {"graphs": await investigation_service.list_graphs(
        str(user.id), chat_id=chat_id or None)}


@router.get("/graphs/{graph_id}/visualize")
async def visualize(
    graph_id: uuid.UUID,
    user: User = Depends(require_approved),
):
    """Nós + arestas completos p/ o canvas (own-check embutido no serviço)."""
    return await investigation_service.visualize(str(user.id), str(graph_id))


@router.delete("/graphs/{graph_id}")
async def delete_graph(
    graph_id: uuid.UUID,
    user: User = Depends(require_approved),
):
    return await investigation_service.delete_graph(str(user.id), str(graph_id))
