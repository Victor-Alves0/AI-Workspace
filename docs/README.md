# Documentation — AI Workspace

Reference guide for AI Workspace. If you just want to get it running, start with the
[main README](../README.md); the pages below go deeper into each area.

## Index

| Document | What for |
|----------|----------|
| [features.md](features.md)           | Full feature catalog, grouped by area |
| [architecture.md](architecture.md)   | System view, components and the lifecycle of a chat turn |
| [turn-pipeline.md](turn-pipeline.md) | One turn end to end: order of operations, every guard/checkpoint, debug-by-layer |
| [harness-coupling.md](harness-coupling.md) | Cross-cutting view: how the layers depend on each other and fail *between* layers; DB-session/loop discipline; maintenance checklist |
| [configuration.md](configuration.md) | Reference for every environment variable (`.env`) |
| [deployment.md](deployment.md)       | LAN/VPS, domain + HTTPS, backup/restore, updates, `APP_SECRET` rotation |
| [public-api.md](public-api.md)       | OpenAI-compatible API (`/v1`) and key management |
| [security.md](security.md)           | Security model, attack surface and recommendations |
| [trust-model.md](trust-model.md)     | Who is trusted with what; why the AI — not the user — is the untrusted actor (PT) |
| [integration-auth.md](integration-auth.md) | How each integration authenticates: which ones are a button, which need a key, and why (PT) |
| [desktop.md](desktop.md)             | Desktop app (Tauri): tray, autostart, build |
| [desktop-updates.md](desktop-updates.md) | How a `.exe` user goes from one version to the next, without losing data (PT) |
| [development.md](development.md)     | Running without Docker, tests, monorepo layout |

## Conventions

- **Local-first:** unless stated otherwise, everything runs on your machine/server via Docker
  Compose. The only required external egress is the model provider (OpenRouter).
- **Per-user secrets** (OpenRouter, Tavily/Brave, voice, bot/OAuth tokens) are saved **in the
  interface**, encrypted at rest — they do **not** go in `.env`.
- **Infrastructure secrets** (`APP_SECRET`, the Postgres password, the Evolution key) go in
  `.env`, which must **never** be committed.
</content>
