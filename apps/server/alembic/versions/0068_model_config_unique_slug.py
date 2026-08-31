"""model custom: ID público único por usuário

O slug é aceito por ``/v1/models`` e ``/v1/chat/completions``. Antes desta
migração dois presets podiam ter o mesmo slug e a resolução escolhia o primeiro
registro retornado pelo banco, aparentando usar uma versão antiga do modelo.

Revision ID: 0068_model_config_unique_slug
Revises: 0067_exec_jobs_durable
Create Date: 2026-08-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0068_model_config_unique_slug"
down_revision: Union[str, None] = "0067_exec_jobs_durable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bancos já existentes podem ter colisões. Mantemos o slug do registro mais
    # antigo e tornamos os demais determinísticos (slug-<8 chars do UUID>) antes
    # de criar a constraint, sem quebrar os chats pois eles guardam o UUID.
    conn = op.get_bind()
    rows = list(conn.execute(sa.text("""
        SELECT id, user_id, slug
        FROM model_configs
        ORDER BY user_id, slug, created_at, id
    """)).mappings())
    seen: set[tuple[str, str]] = set()
    for row in rows:
        user_id = str(row["user_id"])
        raw = row["slug"]
        slug = str(raw).strip() if raw is not None else ""
        if not slug:
            if raw is not None:
                conn.execute(
                    sa.text("UPDATE model_configs SET slug = NULL WHERE id = :id"),
                    {"id": row["id"]},
                )
            continue

        candidate = slug
        if (user_id, candidate) in seen:
            suffix_base = str(row["id"]).replace("-", "")[:8]
            attempt = 0
            while True:
                suffix = f"-{suffix_base}" if attempt == 0 else f"-{suffix_base}-{attempt}"
                candidate = f"{slug[:64 - len(suffix)]}{suffix}"
                if (user_id, candidate) not in seen:
                    break
                attempt += 1
            conn.execute(
                sa.text("UPDATE model_configs SET slug = :slug WHERE id = :id"),
                {"slug": candidate, "id": row["id"]},
            )
        elif candidate != raw:
            conn.execute(
                sa.text("UPDATE model_configs SET slug = :slug WHERE id = :id"),
                {"slug": candidate, "id": row["id"]},
            )
        seen.add((user_id, candidate))

    op.create_unique_constraint(
        "uq_model_config_user_slug", "model_configs", ["user_id", "slug"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_model_config_user_slug", "model_configs", type_="unique")
