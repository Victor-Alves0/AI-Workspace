<div align="center">

# Singularity AI

**A self-hosted, local-first AI workspace — multi-model chat with tools, long-term memory, automations, integrations and a desktop app, all running on _your_ infrastructure.**

[![Stack](https://img.shields.io/badge/backend-FastAPI%20%C2%B7%20Postgres%2016%20%2B%20pgvector-009688)](#architecture)
[![Frontend](https://img.shields.io/badge/frontend-Next.js%2014%20%C2%B7%20Tailwind-000000)](#architecture)
[![Deploy](https://img.shields.io/badge/deploy-Docker%20Compose-2496ED)](#quick-start)
[![Desktop](https://img.shields.io/badge/desktop-Windows%20(Tauri)-6f42c1)](docs/desktop.md)

</div>

---

**Singularity AI** is an OpenWebUI-style AI platform built to **own its own data**: you chat with
any model, give it tools and memory, create automations and integrate with your services — and
none of it leaves your machine except the call to the model provider and whatever **you** tell
the tools to do. Secrets are stored **encrypted at rest** in the database.

It's not a chat wrapper. It's a full stack: a turn orchestrator with tool-calling, vector
memory, a knowledge base (RAG), scheduled automations, messaging channels
(WhatsApp/Telegram/Discord), an OpenAI-compatible public API, end-to-end observability and a
desktop app with a tray icon.

> **Stack:** [FastAPI](https://fastapi.tiangolo.com/) (async) · [SQLAlchemy 2](https://www.sqlalchemy.org/) ·
> **Postgres 16 + [pgvector](https://github.com/pgvector/pgvector)** · [Next.js 14](https://nextjs.org/) (App Router) + Tailwind ·
> [SIFT](https://github.com/Victor-Alves0/SIFT) (tool calling) · [mem0](https://github.com/mem0ai/mem0) (memory) ·
> [OpenRouter](https://openrouter.ai/) · all in **Docker Compose**.

## Table of contents

- [Highlights](#highlights)
- [Features](#features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Optional services](#optional-services)
- [Desktop app](#desktop-app-windows)
- [Documentation](#documentation)
- [Security](#security)
- [Development](#development)
- [License](#license)

## Highlights

- 🔒 **Self-hosted and local-first.** Runs entirely in Docker Compose. The only external egress
  is the model provider and whatever your tools reach. Keys are encrypted in the database.
- 🧠 **Custom models.** Prompt, parameters, tools, capabilities, filters, voice, memory and
  sub-agents — all configurable **per model**, like your own "GPT".
- 🛠️ **Real tools.** Web search, page reading, headless browser, charts, quotes, Gmail/Calendar,
  smart home, GitHub, and **Python code you write**, running in an isolated sandbox.
- ♾️ **Long-term memory** with scopes (global / per model / per chat) and shareable stores, plus
  a **knowledge base (RAG)** with pgvector.
- ⚡ **Automations** — schedules and monitors (price, page, search, RSS) that notify you in the
  app, in the browser (push) or through the channels.
- 💬 **Messaging channels.** Talk to your models over **WhatsApp, Telegram and Discord** — each
  conversation becomes a chat in the sidebar.
- 🔌 **OpenAI-compatible public API** (`/v1/chat/completions`), with per-user keys, limits,
  quotas, cost control and per-key memory.
- 📊 **End-to-end observability** — every request becomes a _trace_ with latency, database time,
  reads/writes and LLM calls, in a waterfall panel.
- 🖥️ **Desktop app (Windows)** with a tray icon, "run in the background" and "start with
  Windows".

## Features

<table>
<tr><td valign="top" width="50%">

**Chat & models**
- Multi-model chat via OpenRouter (any OpenAI-compatible model) and **local models via Ollama**
- Resumable streaming (F5/close doesn't cancel) with a **stop** button
- **Custom models** with their own prompt, parameters, tools, voice and memory
- **Round table**: several models talking to each other, with you steering
- **Sub-agents**: an orchestrator delegates to worker models (sequential/parallel)
- **Temporary chat**, non-destructive context **compaction** and **reference chats**

**Tools (SIFT)**
- Web search (DuckDuckGo/SearXNG/Tavily/Brave) and **deep search**
- Page reading and **headless browser** (AI-controlled Chromium)
- Charts, diagrams (Mermaid/Excalidraw), financial quotes, date/time
- **Video/audio transcription** (YouTube + ~1800 sites)
- **Python code** written by the AI, executed in an **isolated sandbox**

**Content & media**
- **Artifacts** (code/docs/HTML/SVG/Mermaid/CSV) in a dedicated window, with versions
- **Image generation** (native or via a router) and video (Higgsfield)
- **Voice** OpenAI-compatible TTS/STT, incl. local **Kokoro** and voice cloning

</td><td valign="top" width="50%">

**Memory & knowledge**
- **Memory (mem0)** with global/model/chat scopes and shareable stores
- **Knowledge Base (RAG)** — upload documents, pgvector search, citations
- **Second brain**: interlinked notes with a graph; the AI proposes skills and memories
- **Proactive learning**: a background review suggests skills/memories (with approval)

**Automation & channels**
- **Automations** (schedules) + **monitors** (price/page/search/RSS)
- In-app notifications, **Web Push** and delivery through channels
- **WhatsApp** (Evolution/QR or Cloud API), **Telegram** and **Discord**

**Platform & operations**
- **OpenAI-compatible public API** + key management, limits and costs
- **Observability** (traces/spans) and usage **Analytics** (tokens/cost)
- **Codespace**: repository clone + code graph + editor
- **Playground**: benchmarks, comparisons and tool debugging
- **Security**: 2FA (TOTP), audit logs, `APP_SECRET` rotation
- **Desktop app**, **PWA/mobile**, command palette, keyboard shortcuts

</td></tr>
</table>

> The full, detailed list is in **[docs/features.md](docs/features.md)**.

## Architecture

```mermaid
flowchart LR
    subgraph Client
        W["Web (Next.js)"]
        D["Desktop (Tauri)"]
        C["Channels<br/>WhatsApp · Telegram · Discord"]
        X["External apps<br/>(API /v1)"]
    end
    subgraph Backend["Server — FastAPI"]
        O["Turn orchestrator<br/>tool-calling · guards"]
        SIFT["SIFT<br/>tools"]
        MEM["mem0<br/>memory"]
        RAG["Knowledge Base<br/>(RAG)"]
    end
    DB[("Postgres 16<br/>+ pgvector")]
    OR["OpenRouter<br/>(models)"]

    W & D & C & X --> O
    O --> SIFT & MEM & RAG
    O --> OR
    O & MEM & RAG --> DB
```

The code is organized as a monorepo:

```
apps/server    FastAPI — auth, chat orchestrator, SIFT, mem0, RAG, integrations, /v1 API
apps/web       Next.js (App Router) — chat interface, workspace, settings
desktop        Desktop shell (Tauri) — native window, tray, autostart
infra/         Configs for optional services (SearXNG etc.)
```

Docker Compose services:

| Service     | Default | Port (host) | How to enable                                    |
|-------------|:-------:|:-----------:|--------------------------------------------------|
| `db`        | ✅      | internal    | Postgres 16 + pgvector (data + vectors)          |
| `server`    | ✅      | `8000`      | FastAPI API                                       |
| `web`       | ✅      | `3000`      | Next.js interface                                 |
| `searxng`   | opt-in  | `8080`      | `docker compose --profile search up -d`          |
| `kokoro`    | opt-in  | `8880`      | `docker compose --profile voice up -d`           |
| `evolution` | opt-in  | `8081`      | `docker compose --profile whatsapp up -d`        |
| `browser`   | opt-in  | `3009`      | `docker compose --profile browser up -d`         |

Details in **[docs/architecture.md](docs/architecture.md)**.

## Quick start

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) + Docker Compose.

```bash
git clone https://github.com/Victor-Alves0/AI-Workspace.git
cd AI-Workspace
cp .env.example .env

# generate a strong secret and paste it into APP_SECRET in .env:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d --build
```

Open **http://localhost:3000**. The **first user to register becomes admin**. Then, under
**⚙ Settings → Connections → APIs**, paste your **[OpenRouter](https://openrouter.ai/keys) key**
(it's encrypted), pick a model and start chatting.

> Database migrations (Alembic) **run automatically** on server startup. To update later, run
> `./update.sh` (or `git pull && docker compose up -d --build`).

Accessing over the **local network or a VPS**, with a **domain + HTTPS**, or need
**backup/restore**? It's all in **[docs/deployment.md](docs/deployment.md)**.

## Optional services

Heavy features come up on demand via Compose _profiles_:

```bash
# Self-hosted web search (SearXNG, no API key)
docker compose --profile search up -d searxng

# Local voice (Kokoro-FastAPI, OpenAI-compatible TTS)
docker compose --profile voice up -d kokoro

# Unofficial WhatsApp (Evolution API, QR Code)
docker compose --profile whatsapp up -d

# Headless browser (Chromium via browserless) for the browsing tool
docker compose --profile browser up -d
```

## Desktop app (Windows)

A native app that opens the interface in its own window, with a **tray icon**, **"run in the
background"** and **"start with Windows"**.

**⬇️ Download the installer from the [Releases](https://github.com/Victor-Alves0/AI-Workspace/releases/latest) page**
(file `AI.Workspace_x64-setup.exe`, built by CI on every release).

In this version the app does **not** embed the server — keep the stack running
(`docker compose up -d`) and open the app. Full guide in **[docs/desktop.md](docs/desktop.md)**.

## Documentation

| Document | Contents |
|----------|----------|
| [docs/features.md](docs/features.md)         | Full feature catalog |
| [docs/architecture.md](docs/architecture.md) | System view, components and the lifecycle of a turn |
| [docs/configuration.md](docs/configuration.md) | Reference for every environment variable |
| [docs/deployment.md](docs/deployment.md)     | VPS, LAN, HTTPS, backup/restore, updates, secret rotation |
| [docs/public-api.md](docs/public-api.md)     | OpenAI-compatible API + key management |
| [docs/security.md](docs/security.md)         | Security model and recommendations |
| [docs/desktop.md](docs/desktop.md)           | Desktop app (Tauri) |
| [docs/development.md](docs/development.md)    | Running without Docker, tests, project layout |
| [CONTRIBUTING.md](CONTRIBUTING.md)           | How to contribute |

## Security

- Passwords with **Argon2**; sessions via **JWT in an httpOnly cookie** (access + rotating
  refresh) with `token_version` to revoke all sessions; optional **2FA (TOTP)**.
- Per-user secrets encrypted with **Fernet** (key derived from `APP_SECRET`), with a
  **rotation** tool that re-encrypts the database.
- **CORS** restricted to the origins in `WEB_ORIGIN`; security headers on every response;
  **HSTS** under HTTPS; **rate limiting** on login/registration and on the public API.
- **Tool sandbox**: code runs in an isolated subprocess with CPU/memory limits.
- Under `APP_ENV=production`, the server **refuses to start** with a weak `APP_SECRET`.

Details and the threat model in **[docs/security.md](docs/security.md)**. Found a vulnerability?
See [SECURITY.md](SECURITY.md).

## Development

Local setup guide (without Docker), tests and code organization in
**[docs/development.md](docs/development.md)**. To contribute, start with
**[CONTRIBUTING.md](CONTRIBUTING.md)**.

## License

> ⚠️ **This repository does not yet define a license.** Without a `LICENSE` file, the legal
> default is "all rights reserved": third parties have no permission to use, copy or modify it.
> If the intent is to open the project, add a license
> ([choose one here](https://choosealicense.com/)) — MIT/Apache-2.0 for permissive, AGPL-3.0
> for strong copyleft (common in self-hosted apps).
</content>
</invoke>
