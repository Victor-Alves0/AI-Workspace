# Contributing to Singularity AI

Thanks for your interest! This guide covers the basics for proposing changes.

> **License note:** this repository does **not** yet define a license (see
> [README](README.md#license)). Until that is resolved, use and redistribution by third parties
> are not formally authorized. If you plan to contribute from outside, open an issue first to
> align.

## Before you start

1. Read [docs/development.md](docs/development.md) to bring the project up locally.
2. For non-trivial changes, **open an issue** describing the problem/proposal before writing a
   lot of code.

## Workflow

1. Create a branch off `main`.
2. Make the change with tests when it makes sense.
3. Run the suite and the type-check:
   ```bash
   cd apps/server && pytest -q
   cd ../web && npm run build     # frontend type-check
   ```
4. Open a Pull Request describing **what** and **why**.

## Code standards

- **Backend:** Python ≥ 3.11, async FastAPI, SQLAlchemy 2. Changed a model? Generate the Alembic
  migration (`alembic revision --autogenerate -m "..."`).
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind. `next build` validates the types.
- **Tool descriptions** (text the model reads): in **English**, direct — what it does + when to
  use it, no implementation detail.
- **Clean UI:** avoid explanatory paragraphs under headings; use an "i" icon with a tooltip.
- Write code that **matches its surroundings** — same comment density, naming and idiom.

## Don't

- **Don't** commit `.env` or any secret (it's already in `.gitignore`).
- **Don't** edit sources with tools that corrupt UTF-8 (e.g. PowerShell's `Set-Content`).
- **Don't** run destructive migrations without a backup.

## Commits

- Clear messages, in the imperative, explaining the intent.
- Commit only after it's tested.

## Reporting bugs and vulnerabilities

- **Bugs:** open an issue with reproduction steps, expected vs. actual behavior and the
  environment (OS, version).
- **Security vulnerabilities:** do **not** open a public issue — follow [SECURITY.md](SECURITY.md).
</content>
