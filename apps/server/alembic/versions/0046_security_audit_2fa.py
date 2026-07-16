"""Segurança: log de auditoria + 2FA (TOTP).

- `audit_events`: trilha de ações sensíveis (login, troca de senha, alteração de
  segredos, 2FA, backup/restore…). user_id nullable (ex.: login falho de e-mail
  inexistente). Visto pelo usuário (as suas) e pelo admin (todas).
- `users.totp_secret` (cifrado) + `users.totp_enabled`: segundo fator opcional.

Revision ID: 0046_security_audit_2fa
Revises: 0045_inbound_debounce
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0046_security_audit_2fa"
down_revision: Union[str, None] = "0045_inbound_debounce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("user_agent", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        # a Base declarativa adiciona updated_at a todas as tabelas
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")
    op.drop_table("audit_events")
