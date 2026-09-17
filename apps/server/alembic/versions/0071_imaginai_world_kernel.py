"""World Kernel do Imaginai: campanhas, entidades, fatos, conhecimento e eventos.

Revision ID: 0071_imaginai_world_kernel
Revises: 0070_remove_health_notifications
Create Date: 2026-09-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0071_imaginai_world_kernel"
down_revision: str | None = "0070_remove_health_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "imaginai_campaigns",
        *_common(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="Nome da Campanha"),
        sa.Column("system_key", sa.String(64), nullable=False, server_default="dnd5e"),
        sa.Column("system_version", sa.String(32), nullable=False, server_default="5e"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("world_tick", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_event_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("chat_id", name="uq_imaginai_campaign_chat"),
    )
    op.create_index("ix_imaginai_campaigns_user_id", "imaginai_campaigns", ["user_id"])
    op.create_index("ix_imaginai_campaigns_chat_id", "imaginai_campaigns", ["chat_id"])

    op.create_table(
        "imaginai_entities",
        *_common(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("owner_entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("private_notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["imaginai_campaigns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["location_id"], ["imaginai_entities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["owner_entity_id"], ["imaginai_entities.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "campaign_id", "key", name="uq_imaginai_entity_campaign_key"
        ),
    )
    op.create_index("ix_imaginai_entities_campaign_id", "imaginai_entities", ["campaign_id"])
    op.create_index("ix_imaginai_entities_user_id", "imaginai_entities", ["user_id"])
    op.create_index("ix_imaginai_entities_kind", "imaginai_entities", ["kind"])
    op.create_index("ix_imaginai_entities_location_id", "imaginai_entities", ["location_id"])
    op.create_index(
        "ix_imaginai_entities_owner_entity_id", "imaginai_entities", ["owner_entity_id"]
    )
    op.create_index(
        "ix_imaginai_entities_campaign_kind", "imaginai_entities", ["campaign_id", "kind"]
    )

    op.create_table(
        "imaginai_events",
        *_common(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("world_tick", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["imaginai_campaigns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["imaginai_entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_id"], ["imaginai_entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["location_id"], ["imaginai_entities.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("campaign_id", "sequence", name="uq_imaginai_event_sequence"),
    )
    op.create_index("ix_imaginai_events_campaign_id", "imaginai_events", ["campaign_id"])
    op.create_index("ix_imaginai_events_user_id", "imaginai_events", ["user_id"])
    op.create_index("ix_imaginai_events_event_type", "imaginai_events", ["event_type"])
    op.create_index(
        "ix_imaginai_events_campaign_tick", "imaginai_events", ["campaign_id", "world_tick"]
    )

    op.create_table(
        "imaginai_facts",
        *_common(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("predicate", sa.String(96), nullable=False),
        sa.Column("object_entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "value",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "truth_status", sa.String(16), nullable=False, server_default="established"
        ),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="gm"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column("valid_from_tick", sa.Integer(), nullable=True),
        sa.Column("valid_until_tick", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["imaginai_campaigns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["imaginai_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["object_entity_id"], ["imaginai_entities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_event_id"], ["imaginai_events.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_imaginai_facts_campaign_id", "imaginai_facts", ["campaign_id"])
    op.create_index("ix_imaginai_facts_user_id", "imaginai_facts", ["user_id"])
    op.create_index("ix_imaginai_facts_subject_id", "imaginai_facts", ["subject_id"])
    op.create_index("ix_imaginai_facts_predicate", "imaginai_facts", ["predicate"])
    op.create_index("ix_imaginai_facts_truth_status", "imaginai_facts", ["truth_status"])
    op.create_index(
        "ix_imaginai_facts_campaign_subject", "imaginai_facts", ["campaign_id", "subject_id"]
    )

    op.create_table(
        "imaginai_knowledge",
        *_common(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("knower_entity_id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("believes_true", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("learned_at_tick", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["imaginai_campaigns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knower_entity_id"], ["imaginai_entities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["fact_id"], ["imaginai_facts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_event_id"], ["imaginai_events.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "knower_entity_id", "fact_id", name="uq_imaginai_knowledge_knower_fact"
        ),
    )
    op.create_index("ix_imaginai_knowledge_campaign_id", "imaginai_knowledge", ["campaign_id"])
    op.create_index("ix_imaginai_knowledge_user_id", "imaginai_knowledge", ["user_id"])
    op.create_index(
        "ix_imaginai_knowledge_knower_entity_id", "imaginai_knowledge", ["knower_entity_id"]
    )
    op.create_index("ix_imaginai_knowledge_fact_id", "imaginai_knowledge", ["fact_id"])

    op.create_table(
        "imaginai_action_attempts",
        *_common(),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "decision",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["imaginai_campaigns.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["imaginai_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["imaginai_entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["imaginai_events.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_imaginai_action_attempts_campaign_id", "imaginai_action_attempts", ["campaign_id"]
    )
    op.create_index("ix_imaginai_action_attempts_user_id", "imaginai_action_attempts", ["user_id"])
    op.create_index("ix_imaginai_action_attempts_actor_id", "imaginai_action_attempts", ["actor_id"])
    op.create_index(
        "ix_imaginai_action_attempts_action_type", "imaginai_action_attempts", ["action_type"]
    )
    op.create_index("ix_imaginai_action_attempts_status", "imaginai_action_attempts", ["status"])


def downgrade() -> None:
    op.drop_table("imaginai_action_attempts")
    op.drop_table("imaginai_knowledge")
    op.drop_table("imaginai_facts")
    op.drop_table("imaginai_events")
    op.drop_table("imaginai_entities")
    op.drop_table("imaginai_campaigns")
