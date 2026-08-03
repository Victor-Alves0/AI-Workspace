"""Grafo de Investigação: nós/arestas TIPADOS que a IA curte investigando algo que
NÃO é código-fonte que temos em disco — um alvo black-box (recon), um binário sem
fonte (engenharia reversa), ou o comportamento observado de um sistema.

É o análogo do codegraph para o resto: o codegraph auto-preenche o caso "tenho o
fonte"; este é preenchido pela IA conforme ela sonda/observa, e os dois se cruzam
(uma aresta `maps_to` liga um nó de investigação a um fqn do codegraph — Fase 2).

Diferente do brain (que resolve arestas por [[wikilink]] em texto), aqui os nós têm
`type` + `props` (JSONB) + `confidence` — recon é incerto, então cada nó/aresta
carrega o nível de confiança (certain|inferred|possible), igual às arestas do
codegraph. Vocabulário de `type`/`rel` é livre, com sugestões na tool.

Três tabelas: o container (`investigation_graphs`), os nós e as arestas. Persistido
em DB (sobrevive a restart — ao contrário do registro em-memória `generation._active`).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class InvestigationGraph(Base):
    __tablename__ = "investigation_graphs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # o alvo sendo investigado (host, url, caminho do binário, nome do serviço) — livre
    target: Mapped[str] = mapped_column(Text, default="")
    # "recon" (alvo de rede) | "re" (engenharia reversa de binário) |
    # "behavior" (comportamento observado) | "generic"
    kind: Mapped[str] = mapped_column(String(16), default="generic")
    # vínculos opcionais: o chat que originou a investigação e/ou um projeto do
    # Codespace (p/ o cruzamento com o codegraph na Fase 2). SET NULL: apagar o
    # chat/projeto não apaga o grafo.
    chat_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True, index=True
    )
    codespace_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("codespace_projects.id", ondelete="SET NULL"), nullable=True
    )


class InvestigationNode(Base):
    __tablename__ = "investigation_nodes"

    graph_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_graphs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # tipo livre, vocabulário sugerido: host | port | service | endpoint | page |
    # form | param | function | import | string | section | syscall | behavior |
    # finding | note
    type: Mapped[str] = mapped_column(String(48), default="node")
    # rótulo humano — a chave de idempotência (por (graph, type, label)) é resolvida
    # no serviço; guardado como Text porque labels podem ser longos (URLs etc.)
    label: Mapped[str] = mapped_column(Text, default="")
    # propriedades arbitrárias observadas: {"banner": ..., "status": 200, "method": ...}
    props: Mapped[dict] = mapped_column(JSONB, default=dict)
    # certain | inferred | possible — recon é incerto; espelha o codegraph
    confidence: Mapped[str] = mapped_column(String(12), default="inferred")

    __table_args__ = (
        Index("ix_investigation_nodes_graph_type", "graph_id", "type"),
    )


class InvestigationEdge(Base):
    __tablename__ = "investigation_edges"

    graph_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_graphs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    src_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_nodes.id", ondelete="CASCADE"), index=True
    )
    dst_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigation_nodes.id", ondelete="CASCADE"), index=True
    )
    # relação livre, sugestões: hosts | exposes | serves | links_to | has_param |
    # calls | imports | reads | writes | maps_to (cruzamento c/ codegraph)
    rel: Mapped[str] = mapped_column(String(48), default="rel")
    props: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[str] = mapped_column(String(12), default="inferred")
