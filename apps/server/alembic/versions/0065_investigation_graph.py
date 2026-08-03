"""Grafo de Investigação: nós/arestas tipados que a IA curte investigando algo sem
código-fonte em disco (recon black-box, engenharia reversa de binário, comportamento).

Análogo do codegraph para o resto: três tabelas — o container, os nós e as arestas.
Cada nó/aresta carrega `confidence` (certain|inferred|possible) como o codegraph.

Revision ID: 0065_investigation_graph
Revises: 0064_task_ledger
Create Date: 2026-08-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_investigation_graph"
down_revision: Union[str, None] = "0064_task_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "investigation_graphs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("target", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="generic"),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("codespace_project_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["codespace_project_id"], ["codespace_projects.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_investigation_graphs_user_id", "investigation_graphs", ["user_id"])
    op.create_index("ix_investigation_graphs_chat_id", "investigation_graphs", ["chat_id"])

    op.create_table(
        "investigation_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("graph_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False, server_default="node"),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("props", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.String(length=12), nullable=False, server_default="inferred"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["investigation_graphs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_investigation_nodes_graph_id", "investigation_nodes", ["graph_id"])
    op.create_index("ix_investigation_nodes_user_id", "investigation_nodes", ["user_id"])
    op.create_index("ix_investigation_nodes_graph_type", "investigation_nodes", ["graph_id", "type"])

    op.create_table(
        "investigation_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("graph_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("src_id", sa.Uuid(), nullable=False),
        sa.Column("dst_id", sa.Uuid(), nullable=False),
        sa.Column("rel", sa.String(length=48), nullable=False, server_default="rel"),
        sa.Column("props", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.String(length=12), nullable=False, server_default="inferred"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["graph_id"], ["investigation_graphs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["src_id"], ["investigation_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dst_id"], ["investigation_nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_investigation_edges_graph_id", "investigation_edges", ["graph_id"])
    op.create_index("ix_investigation_edges_user_id", "investigation_edges", ["user_id"])
    op.create_index("ix_investigation_edges_src_id", "investigation_edges", ["src_id"])
    op.create_index("ix_investigation_edges_dst_id", "investigation_edges", ["dst_id"])


def downgrade() -> None:
    op.drop_table("investigation_edges")
    op.drop_table("investigation_nodes")
    op.drop_table("investigation_graphs")
