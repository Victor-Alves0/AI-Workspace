"""Caixa de texto do painel de um agente: `POST /chats/{chat}/agents/{ref}/message`.

Agente trabalhando → a mensagem entra no loop dele e a resposta é JSON
`{"mode": "steer"}` (o efeito aparece no stream do chat). Agente que já terminou →
abre uma continuação e responde em SSE (`progress` … `done`/`error`). Ver
agent_followup.py.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import require_approved
from ..db import get_db
from ..models import User
from . import agent_followup
from .turn_setup import _get_owned_chat

router = APIRouter()


class AgentMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    think: bool = False


@router.post("/{chat_id}/agents/{ref}/message")
async def message_agent(
    chat_id: uuid.UUID, ref: str, body: AgentMessageIn,
    user: User = Depends(require_approved), db: AsyncSession = Depends(get_db),
):
    await _get_owned_chat(db, chat_id, user)
    if not ref or len(ref) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "agente inválido")
    try:
        q = await agent_followup.send(user, chat_id, ref, body.content.strip(), think=body.think)
    except agent_followup.FollowupError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    if q is None:
        return {"mode": "steer"}

    async def stream():
        async for ev in agent_followup.listen(chat_id, ref, q):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
