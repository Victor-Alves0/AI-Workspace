"""Remove alarmes de saúde do feed de notificações.

Os eventos continuam persistidos em health_events e visíveis em Administração → Saúde.

Revision ID: 0070_remove_health_notifications
Revises: 0069_remote_hosts
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0070_remove_health_notifications"
down_revision: str | None = "0069_remote_hosts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Prefixo exclusivo usado pelo alarme antigo de health_service._maybe_alarm.
    op.execute("DELETE FROM notifications WHERE title LIKE 'Saúde:%'")


def downgrade() -> None:
    # Notificações removidas não são recriadas: os dados canônicos permanecem em
    # health_events e inventar cópias no downgrade seria incorreto.
    pass
