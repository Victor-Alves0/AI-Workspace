from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    title: str = "Novo Chat"
    model: str = ""
    system_prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    folder_id: uuid.UUID | None = None
    model_config_id: uuid.UUID | None = None


class ChatUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    title: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    params: dict[str, Any] | None = None
    archived: bool | None = None
    pinned: bool | None = None
    folder_id: uuid.UUID | None = None
    model_config_id: uuid.UUID | None = None
    # memória por-chat: {"write": "...", "read": {...}} (null = herda modelo/perfil)
    memory_config: dict[str, Any] | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    tool_calls: list | None = None
    tool_call_id: str | None = None
    tokens: int | None = None
    cost: float | None = None
    usage: dict[str, Any] | None = None
    reasoning: dict[str, Any] | None = None
    tool_events: list[dict[str, Any]] | None = None
    memories_used: list[dict[str, Any]] | None = None
    attachments: list[dict[str, Any]] | None = None
    is_summary: bool = False
    compacted: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class MessageEdit(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    title: str
    model: str
    system_prompt: str | None
    params: dict[str, Any]
    archived: bool
    pinned: bool
    # "Duração do Chat" (automações): view_once = o front apaga ao abrir e sair
    view_once: bool = False
    folder_id: uuid.UUID | None
    model_config_id: uuid.UUID | None
    memory_config: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ChatDetail(ChatOut):
    messages: list[MessageOut] = []


class Attachment(BaseModel):
    type: str = Field(pattern=r"^(image|file)$")
    name: str = Field(default="", max_length=255)
    url: str | None = Field(default=None, max_length=8_000_000)  # data URL (imagem)
    text: str | None = Field(default=None, max_length=200_000)  # conteúdo (arquivo texto)
    # doc binário p/ extração server-side (PDF/DOCX/XLSX/PPTX): base64 + mime
    data: str | None = Field(default=None, max_length=16_000_000)
    mime: str | None = Field(default=None, max_length=200)


class SendMessageIn(BaseModel):
    # pode ser vazio quando há anexos (ex.: só uma imagem, sem texto)
    content: str = Field(default="", max_length=100_000)
    # skills invocadas ad-hoc via "$" no promptbox (além das equipadas no modelo)
    skill_ids: list[uuid.UUID] = Field(default_factory=list)
    # anexos (imagens/arquivos) — máx. 6, processados conforme as capacidades do modelo
    attachments: list[Attachment] = Field(default_factory=list, max_length=6)
    # "@" no promptbox: roteia SÓ ESTE turno a outro agente (ModelConfig), sem mudar
    # o modelo padrão do chat. None = usa o modelo do chat.
    agent_model_config_id: uuid.UUID | None = None


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: uuid.UUID | None = None


class FolderOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class PresetCreate(BaseModel):
    name: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
