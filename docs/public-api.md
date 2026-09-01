# Public API

AI Workspace exposes an **OpenAI-compatible** API at `/v1`. An OpenAI client or SDK works by
changing only the `base_url` and the key — no adapter.

## Authentication

Every call uses a **Bearer token** in the form `aw-<prefix>-<secret>`:

```
Authorization: Bearer aw-xxxxxxxxxxxx-xxxxxxxx...
```

Clients that don't allow customizing the header also accept `X-API-Key`. The secret has 256 bits
of entropy and is shown **only once** at creation; the database stores only the SHA-256.

Create and manage keys under **Workspace → API** (or in Settings). Each key has:

- **Name** and state (active / disabled / revoked / expired), with **instant revocation** and
  **regeneration**.
- **Permissions (scopes):** `chat`, `models:read`, `memory:read`, `memory:write`, `files:read`,
  `files:write`, `usage:read`.
- **Limits:** requests per minute (RPM), per day (RPD), monthly, input/output tokens,
  **concurrency** and **budget (US$)** with automatic blocking.
- **Model policy:** allow all or only a list (prevents access to future versions).
- **Memory mode** per key (see below), **IP allowlist** (CIDR) and event **webhooks**.

## Endpoints

### Chat Completions

`POST /v1/chat/completions` — OpenAI-compatible. Supports:

- **Streaming** (`"stream": true`), **synchronous** and **asynchronous** (`"background": true`).
- **Platform mode** (default): reuses the app's orchestrator — server tools, memory, knowledge
  and the model's preset. Extra metadata goes in an `aiworkspace` field.
- **Passthrough mode**: if the client sends `tools` (classic function calling), the call is
  forwarded to the provider untouched.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer aw-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

With the OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="aw-...")
resp = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

### Models

- `GET /v1/models` — lists the models available to the key.
- `GET /v1/models/{id}` — details of a model.

### Memory, files, usage and account

- `GET/POST/DELETE /v1/memories` — CRUD + clear/export/import (respecting the key's memory-mode
  isolation). Requires the `memory:*` scope.
- `GET/POST /v1/files` — Knowledge Base documents. Requires the `files:*` scope.
- `GET /v1/usage` — key consumption (requests, tokens, cost). Requires `usage:read`.
- `GET /v1/account` — account/limit information.

## Memory modes (per key)

Memory behavior is set **on the key**, which is essential for anyone reselling the API:

| Mode        | Behavior |
|-------------|----------|
| `none`      | No memory. |
| `request`   | Only the request's own context. |
| `persistent`| Shares the app's memory (of the key owner). |
| `shared`    | Same as `persistent`, making explicit that several keys see the same. |
| `key`       | Isolated per key (`apikey:<id>`). |
| `end_user`  | Isolated per **end user** (`enduser:<key>:<id>`) — the right mode for reselling. |

## Limits and errors

Errors follow the OpenAI format (`{"error": {message, type, code}}`). When a limit is hit, the
response carries the appropriate code and, where applicable, the `Retry-After` header:

- `rate_limit_exceeded` / `concurrency_limit` → HTTP 429
- `daily_quota_exceeded` / `monthly_quota_exceeded` / `*_token_quota_exceeded` → HTTP 429
- `budget_exceeded` → HTTP 402
- `invalid_api_key` / `key_revoked` / `key_expired` → HTTP 401
- `insufficient_scope` / `ip_not_allowed` → HTTP 403

## Webhooks

A key can declare a webhook URL (signed with HMAC-SHA256) to receive events such as
`limit.reached` and `request.error`, useful for observing consumption on the integrator's side.

## Dashboard

The panel under **Workspace → API** lists the keys, allows create/revoke, shows real-time
monitoring, usage and cost per model, recent errors, and includes the documentation and an
integrated playground.
</content>
