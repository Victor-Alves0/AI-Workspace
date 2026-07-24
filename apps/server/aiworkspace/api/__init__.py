"""API pública: chaves de API, endpoints compatíveis com OpenAI e painel.

Camadas:
  - `keys_service`  — geração/verificação de chave, políticas (modelo, memória)
  - `limits`        — rate limit, cotas, concorrência e orçamento por chave
  - `webhooks`      — notificação de eventos (limite atingido, revogação, erro)
  - `runner`        — execução de um turno vindo da API (reusa `chat.run_turn`)
  - `v1_routes`     — /v1/chat/completions, /v1/models (compat OpenAI)
  - `mgmt_routes`   — /v1/memories, /v1/files, /v1/usage, /v1/account
  - `keys_routes`   — /api-keys (CRUD pelo painel, autenticado por cookie)
"""
