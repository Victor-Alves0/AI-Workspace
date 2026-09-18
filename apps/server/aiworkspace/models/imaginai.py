"""Estado autoritativo do Imaginai.

O modelo de linguagem nunca é a fonte da verdade deste domínio: ele pode propor
intenções e lore, mas só estas tabelas definem o que existe, onde está, quem sabe
o quê e quais mudanças realmente aconteceram.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..crypto import EncryptedText
from ..db import Base


class ImaginaiCampaign(Base):
    __tablename__ = "imaginai_campaigns"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Um chat representa uma mesa/campanha. Apagar o chat remove todo o mundo.
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="Nome da Campanha")
    # Plugin de regras. Novos sistemas entram no registry sem mudar o schema.
    system_key: Mapped[str] = mapped_column(String(64), default="dnd5e")
    system_version: Mapped[str] = mapped_column(String(32), default="5e")
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Relógio abstrato monotônico; calendários ficam em settings até haver um
    # plugin de calendário. Nunca usamos o relógio real como tempo diegético.
    world_tick: Mapped[int] = mapped_column(Integer, default=0)
    next_event_sequence: Mapped[int] = mapped_column(Integer, default=1)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Combate em andamento (ordem de iniciativa, rodada, de quem é a vez) — ver
    # imaginai/combat.py. None fora de combate. Fica separado de `settings` porque é
    # ESTADO DO MUNDO, não configuração da mesa.
    encounter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ImaginaiEntity(Base):
    __tablename__ = "imaginai_entities"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # character | npc | creature | item | location | faction | object | ...
    kind: Mapped[str] = mapped_column(String(32), index=True)
    # Identificador estável dentro da campanha; nomes podem mudar e se repetir.
    key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Estado público + ficha específica do sistema. Ex.: {"dnd5e": {"hp": ...}}.
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Lore secreta/persona completa não deve escapar no snapshot geral da UI.
    private_notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("campaign_id", "key", name="uq_imaginai_entity_campaign_key"),
        Index("ix_imaginai_entities_campaign_kind", "campaign_id", "kind"),
    )


class ImaginaiEvent(Base):
    """Ledger append-only dos acontecimentos confirmados do mundo."""

    __tablename__ = "imaginai_events"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    world_tick: Mapped[int] = mapped_column(Integer, default=0)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # public | party | gm; controla o contexto que cada ator pode receber.
    visibility: Mapped[str] = mapped_column(String(16), default="public")

    __table_args__ = (
        UniqueConstraint("campaign_id", "sequence", name="uq_imaginai_event_sequence"),
        Index("ix_imaginai_events_campaign_tick", "campaign_id", "world_tick"),
    )


class ImaginaiFact(Base):
    """Fato normalizado, rumor ou proposta de lore.

    `truth_status=proposed` nunca entra como verdade no contexto de jogo até ser
    promovido. `locked` impede geradores de lore de reescrever fatos canônicos.
    """

    __tablename__ = "imaginai_facts"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    predicate: Mapped[str] = mapped_column(String(96), index=True)
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True
    )
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    # canonical | established | rumor | proposed | false
    truth_status: Mapped[str] = mapped_column(String(16), default="established", index=True)
    visibility: Mapped[str] = mapped_column(String(16), default="gm")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_events.id", ondelete="SET NULL"), nullable=True
    )
    valid_from_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_until_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_imaginai_facts_campaign_subject", "campaign_id", "subject_id"),
    )


class ImaginaiKnowledge(Base):
    """O que uma entidade acredita saber; separa verdade global de conhecimento."""

    __tablename__ = "imaginai_knowledge"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    knower_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="CASCADE"), index=True
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_facts.id", ondelete="CASCADE"), index=True
    )
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_events.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    believes_true: Mapped[bool] = mapped_column(Boolean, default=True)
    learned_at_tick: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "knower_entity_id", "fact_id", name="uq_imaginai_knowledge_knower_fact"
        ),
    )


class ImaginaiActionAttempt(Base):
    """Auditoria de toda ação pedida, inclusive as recusadas pelo kernel."""

    __tablename__ = "imaginai_action_attempts"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_entities.id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    input: Mapped[dict] = mapped_column(JSONB, default=dict)
    # allowed | blocked | requires_check | needs_adjudication | resolved
    status: Mapped[str] = mapped_column(String(20), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[dict] = mapped_column(JSONB, default=dict)
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("imaginai_events.id", ondelete="SET NULL"), nullable=True
    )


class ImaginaiJournalEntry(Base):
    """Anotação privada escrita pelo jogador; não é fato canônico do mundo."""

    __tablename__ = "imaginai_journal_entries"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("imaginai_campaigns.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="Sem título")
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    __table_args__ = (
        Index(
            "ix_imaginai_journal_campaign_updated",
            "campaign_id",
            "updated_at",
        ),
    )
