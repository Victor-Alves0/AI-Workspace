"""Voz Local apontando para o antigo serviço Kokoro do compose → voz embutida.

O serviço `kokoro` saiu do compose (a voz embutida roda no próprio processo). Uma
conexão de Voz Local no endereço padrão dele (porta 8880, `localhost`/`kokoro`)
deixaria a fala quebrada: ela some, e os modelos que usavam o provedor "local" desse
usuário passam para o "builtin" (as vozes têm os mesmos nomes).

Revision ID: 0081_voice_local_to_builtin
Revises: 0080_whatsapp_history
Create Date: 2026-09-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0081_voice_local_to_builtin"
down_revision: str | None = "0080_whatsapp_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VELHOS = r"""
    SELECT substr(key, 7) AS user_id FROM app_settings
    WHERE key LIKE 'voice:%'
      AND coalesce(value->'v'->>'base_url', '')
          ~ '^https?://(localhost|127\.0\.0\.1|host\.docker\.internal|kokoro)[:]8880(/|$)'
"""


def upgrade() -> None:
    op.execute(
        "UPDATE model_configs SET filter_config = jsonb_set(filter_config, '{voice}', "
        "((filter_config->'voice') - 'tts_model') || '{\"tts_provider\": \"builtin\"}'::jsonb) "
        f"WHERE user_id::text IN ({_VELHOS}) "
        "AND filter_config->'voice'->>'tts_provider' = 'local'"
    )
    # destrutivo-aprovado: só a conexão que aponta para o serviço removido do compose (inútil sem ele)
    op.execute(f"DELETE FROM app_settings WHERE key LIKE 'voice:%' AND substr(key, 7) IN ({_VELHOS})")


def downgrade() -> None:
    # o serviço antigo não existe mais: não há o que restaurar
    pass
