## What and why

<!-- What does this change and what problem does it solve? Link the issue: Closes #123 -->

## How it was tested

<!-- Tests added/run, manual checks, screenshots for UI changes. -->

## Checklist

- [ ] `cd apps/server && pytest -q` passes
- [ ] `cd apps/web && npx tsc --noEmit` passes (for frontend changes)
- [ ] New environment variables are documented in `docs/configuration.md` or `.env.example`
- [ ] Model changes come with an Alembic migration
