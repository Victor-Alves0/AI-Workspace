"""Pastas, chats, mensagens e presets."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..crypto import EncryptedText
from ..db import Base


class Folder(Base):
    __tablename__ = "folders"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )


class Chat(Base):
    __tablename__ = "chats"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    # modelo personalizado (ModelConfig) escolhido, se houver — define ferramentas
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), default="Novo Chat")
    system_prompt: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    model: Mapped[str] = mapped_column(String(255), default="")
    # modo do chat: "single" (padrão) | "roundtable" (mesa-redonda multi-modelo)
    mode: Mapped[str] = mapped_column(String(16), default="single")
    # participantes da mesa-redonda: [{id, model, model_config_id?, name, avatar?,
    #   color, persona?}]. Vazio em chats normais.
    participants: Mapped[list] = mapped_column(JSONB, default=list)
    # config da mesa: {turn_policy: round_robin|manual|moderator, moderator: {...},
    #   max_rounds, next: <participant id>}
    roundtable_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # parâmetros do modelo (temperature, top_p, etc.)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    # memória por-chat (nullable = herda do modelo/perfil):
    #   {"write": "global|model|chat|off", "read": {"global": b, "model": b, "chat": b}}
    memory_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # conhecimento por-chat (nullable = herda do modelo/perfil):
    #   {"enabled": b, "bases": [<knowledge_base id>], "mode": "auto|tool", "k": int}
    knowledge_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # etiquetas livres p/ organizar/filtrar conversas (["trabalho", "ideias", ...])
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # compartilhamento: quando setado, o chat tem um link público read-only
    # (/shared/<public_id>). None = privado. Revogar = voltar a None.
    public_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, unique=True, index=True
    )
    # "Duração do Chat" (chats criados por automações): apagado pelo scheduler
    # quando expires_at vence; view_once = apagado quando o usuário abre e sai.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    view_once: Mapped[bool] = mapped_column(Boolean, default=False)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    # system | user | assistant | tool
    role: Mapped[str] = mapped_column(String(16))
    # conteúdo cifrado em repouso (transparente); legado em texto puro é lido normal
    content: Mapped[str] = mapped_column(EncryptedText, default="")
    # tool_calls emitidos pelo assistant (formato OpenAI), se houver
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # vincula uma mensagem role=tool ao tool_call que a originou
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[float | None] = mapped_column(nullable=True)
    # registro ponta-a-ponta desta mensagem: origem (modelo/provider), tokens de
    # entrada/saída, custo, finish_reason — ver chat.routes._usage_record
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # raciocínio do modelo ("thinking"), quando exposto: {"text": ..., "seconds": ...}
    reasoning: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # usos de ferramenta neste segmento: [{"kind":"call|result","name":...,"data":...}]
    tool_events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # memórias (mem0) injetadas nesta resposta: [{"id":...,"text":...,"scope":...}]
    memories_used: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # anexos da mensagem do usuário (imagens/arquivos):
    #   [{"type":"image","name":...,"url":<data url>} | {"type":"file","name":...,"text":...}]
    attachments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # marca a mensagem-resumo (divisor) criada pela compactação de contexto;
    # renderizada como um divisor visual no chat (o texto do resumo fica no nó do
    # Grafo de contexto), e é a única mensagem-resumo que ENTRA no contexto da IA.
    is_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    # compactação NÃO-destrutiva: a mensagem continua visível ao usuário, mas fica
    # FORA do contexto enviado ao modelo (substituída pelo resumo do divisor).
    compacted: Mapped[bool] = mapped_column(Boolean, default=False)
    # mesa-redonda: quem produziu esta fala (assistant multi-modelo). null = humano.
    #   {"id":..., "name":..., "model":..., "color":...}
    speaker: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="messages")


class ChatCompaction(Base):
    """Checkpoint de compactação: cada vez que o contexto é compactado, guardamos
    o resumo gerado. A UI mostra o histórico (timeline) e permite FIXAR (pin) qual
    resumo deve ser o contexto ativo da IA."""

    __tablename__ = "chat_compactions"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(EncryptedText, default="")
    # quantas mensagens foram resumidas neste checkpoint (p/ exibir na timeline)
    message_count: Mapped[int] = mapped_column(default=0)
    # exatamente um checkpoint fica fixado por chat (o checkpoint ativo/restaurado)
    pinned: Mapped[bool] = mapped_column(Boolean, default=True)
    # snapshot completo das mensagens ANTES da compactação — permite RESTAURAR
    # exatamente aquele ponto da conversa (feature de checkpoint p/ pesquisa etc.)
    snapshot: Mapped[list] = mapped_column(JSONB, default=list)
    # ramificação: checkpoint de onde este derivou (o ativo no momento). Forma uma
    # ÁRVORE — restaurar um ponto e seguir cria um ramo, mantendo os dois.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_compactions.id", ondelete="SET NULL"), nullable=True
    )
    # nome personalizado do checkpoint (definido pelo usuário; opcional)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Preset(Base):
    """Configuração reutilizável de modelo + parâmetros."""

    __tablename__ = "presets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
