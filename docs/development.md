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
- A test carries the **reason it exists** in its docstring — which real failure it prevents. A
  test nobody can justify is a test nobody dares to delete.

Three kinds of test share the suite:

| Kind | Example | What it catches |
|---|---|---|
| Behavior | `test_run_turn_integration`, `test_civitai` | the turn/tool does what it promises |
| Secrecy | `test_imaginai_visibility`, `test_integration_clients` | what must **not** leak: hidden entities, a token inside an error message |
| Invariant | `test_repo_invariants`, `test_version_sync` | rules the repository must keep: one migration chain, every model exported, every env var documented (`.env.example` or `docs/configuration.md`), every hover-only control still reachable by touch |

The invariants are the cheapest way to keep a convention alive — they fail on the next commit
that forgets it, instead of on a phone, months later. When you add a rule that lives in someone's
head ("always do X when adding Y"), add it there.

### Database battery

Everyone's data lives in Postgres, and every migration runs **unattended** at boot on the
database of whoever updates. Two layers guard it:

| Layer | Runs | What it catches |
|---|---|---|
| `tests/test_migration_lint.py` | always (reads the code, no DB) | `NOT NULL` column without `server_default` on an existing table; drop/delete/type change without a declared reason; migration importing app code; identifier > 63 bytes; `CREATE EXTENSION` without `IF NOT EXISTS`; `CONCURRENTLY` inside the migration transaction |
| `tests/db/` | only with `TEST_DATABASE_URL` | the SQL actually running on a real Postgres (with pgvector) |

`tests/db/` creates a throwaway database per test (`aiw_test_<hex>`) next to the one you point
at, and drops it afterwards — the database in the URL is never touched:

- **every migration over data** — climbs the chain one step at a time with **every table
  populated** (a generic seeder fills all columns by reflection) and fails if a step errors or
  loses rows;
- **models vs. migrated schema** — a column the model uses but no migration created is a 500 in
  production on the first query;
- **downgrade and back** over data (deploy rollback);
- **account deletion** through the ORM, as the endpoint does: it must succeed and must not take
  another user's rows with it; every FK to `users` must declare `ON DELETE`;
- **backup → restore** with the exact panel commands (`aiworkspace/db_restore.py`): row-for-row
  round trip, restoring an **older-version** backup and migrating it to head, a truncated dump
  refused before touching the database, and a failure in the **last** restore statement leaving
  the database exactly as it was.

```bash
cd apps/server
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres pytest tests/db -q
# pg_dump/pg_restore/psql outside the PATH? point PG_BIN at their folder
```

CI (`.github/workflows/tests.yml`) runs the whole suite against a `pgvector/pgvector:pg16`
service on every push and PR.

A migration that deletes data **on purpose** says so in the file, with the reason:

```python
# destrutivo-aprovado: only system-generated copies; the canonical data stays in health_events
```

## Migrations

When you change a model, generate a migration:

```bash
cd apps/server
alembic revision --autogenerate -m "short description"
alembic upgrade head
```

Migrations are applied **automatically** on server startup, all pending ones in **one
transaction** (a failure rolls everything back — but the server then refuses to boot until it is
fixed). So, before committing a migration:

- a revision id of at most **32 characters** (`alembic_version.version_num` is `VARCHAR(32)`);
- a new `NOT NULL` column on an existing table needs a `server_default` (or add it nullable,
  backfill with `UPDATE`, then tighten);
- never import `aiworkspace.models` in a migration — freeze the columns/SQL in the file;
- run `pytest tests/db` against a local Postgres (see *Database battery*).

## Style and principles

- **Tool descriptions** (the text the model reads) in **English**, direct: what it does + when to
  use it, no implementation detail.
- **Clean UI**: avoid explanatory paragraphs under headings; prefer an "i" icon with a tooltip.
- **Never** edit repo sources with tools that rewrite the encoding (e.g. PowerShell's
  `Set-Content` corrupts UTF-8) — use an editor that preserves UTF-8.
- **Commit** only after everything is tested; `.env` is **never** committed.

See also [CONTRIBUTING.md](../CONTRIBUTING.md).
</content>
