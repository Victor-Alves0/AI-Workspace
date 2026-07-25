# Development

How to run AI Workspace locally without Docker, how the code is organized and how to run the tests.

## Monorepo layout

```
apps/
  server/                 FastAPI backend
    aiworkspace/
      chat/               Turn orchestrator, chat routes, guards
      api/                Public API (/v1): routes, keys, limits, webhooks
      integrations/       Google, Tuya, GitHub, channels, transcription…
      memory/             mem0
      knowledge/          Knowledge Base (RAG)
      codespace/          Code graph, projects
      tracing/            Observability (traces/spans)
      tools/              SIFT, loader, sandbox
      models/             ORM (SQLAlchemy)
      main.py             App, middleware, route registration
    alembic/versions/     55 migrations
    tests/                pytest suite (hermetic)
    pyproject.toml
  web/                    Next.js frontend (App Router)
    app/                  Pages
    components/           Components
    lib/                  HTTP client, SSE, types, desktop bridge
desktop/                  Tauri shell (Rust)
infra/                    Configs for optional services
docs/                     This documentation
```

## Backend without Docker

Requires **Python ≥ 3.11** and a reachable **Postgres 16 with pgvector**.

```bash
cd apps/server
python -m venv .venv
. .venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

# bring up a Postgres with pgvector (example):
docker run -d -p 5432:5432 \
  -e POSTGRES_PASSWORD=aiworkspace -e POSTGRES_USER=aiworkspace -e POSTGRES_DB=aiworkspace \
  pgvector/pgvector:pg16

export APP_SECRET="dev-insecure-only"       # Windows PowerShell: $env:APP_SECRET="..."
alembic upgrade head
uvicorn aiworkspace.main:app --reload
```

## Frontend without Docker

```bash
cd apps/web
npm install
npm run dev
```

`next build` also serves as the frontend **type-check**.

> Docker images **bake** the code at build time — when you change code, run
> `docker compose up -d --build` to apply it. `web` is a production build (`next start`), so UI
> changes **require** `docker compose build web`.

## Tests

The backend suite is **hermetic** (no network/binaries — I/O seams are replaced by fakes):

```bash
cd apps/server
pytest -q
```

Conventions:

- Tests live in `apps/server/tests`. `pytest` is a `[dev]` dependency — it does **not** go in the
  production image.
- When running inside the rebuilt container, install the test deps first
  (`pip install --user pytest pytest-asyncio`) and copy the `tests/` folder, since the prod image
  doesn't include them.
- Prefer tests that exercise **decision and shape** (parsing, routing, guards) over real I/O.

## Migrations

When you change a model, generate a migration:

```bash
cd apps/server
alembic revision --autogenerate -m "short description"
alembic upgrade head
```

Migrations are applied **automatically** on server startup.

## Style and principles

- **Tool descriptions** (the text the model reads) in **English**, direct: what it does + when to
  use it, no implementation detail.
- **Clean UI**: avoid explanatory paragraphs under headings; prefer an "i" icon with a tooltip.
- **Never** edit repo sources with tools that rewrite the encoding (e.g. PowerShell's
  `Set-Content` corrupts UTF-8) — use an editor that preserves UTF-8.
- **Commit** only after everything is tested; `.env` is **never** committed.

See also [CONTRIBUTING.md](../CONTRIBUTING.md).
</content>
