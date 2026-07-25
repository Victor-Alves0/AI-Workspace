# Configuration

All **infrastructure** configuration comes from environment variables, read from a `.env` file
at the root. Start by copying the template:

```bash
cp .env.example .env
```

[`.env.example`](../.env.example) is the source of truth — every variable is commented there.
This page organizes and explains the main ones.

> **Per-user secrets** (OpenRouter, Tavily/Brave, voice provider, bot/OAuth tokens) do **not**
> go in `.env`: each user saves their own in the interface, encrypted at rest. Only
> **infrastructure** secrets live in `.env`.

## Security

| Variable        | Default       | Description |
|-----------------|---------------|-------------|
| `APP_ENV`       | `development` | `production` hardens CORS (accepts only the exact origins in `WEB_ORIGIN`) and **refuses to start** with a weak `APP_SECRET`. |
| `APP_SECRET`    | —             | **Must be changed.** Signs the JWTs and **derives the encryption key** for the secrets stored in the database. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `ENABLE_SIGNUP` | `true`        | Allows registration. The **first** user becomes admin; afterward consider `false` to close it. |

> **`APP_SECRET` does not grant database access** (that's the Postgres password) — it encrypts
> the secret **values**. Changing it carelessly makes those values unreadable (the app treats
> them as "not configured") and logs everyone out. Need to change it without losing the secrets?
> Use **rotation** (see [deployment.md](deployment.md#rotating-the-app_secret)).

## Database

| Variable            | Default       | Description |
|---------------------|---------------|-------------|
| `POSTGRES_USER`     | `aiworkspace` | Postgres user. |
| `POSTGRES_PASSWORD` | `aiworkspace` | **Change it for a real install.** |
| `POSTGRES_DB`       | `aiworkspace` | Database name. |
| `DATABASE_URL`      | (assembled)   | Only touch it to run the server **outside** Docker. |
| `DB_PORT` / `DB_BIND` | (commented) | Postgres is **not** published on the host by default. To connect an external client (psql/DBeaver), copy `docker-compose.override.yml.example` and adjust. |

## Models (OpenRouter)

| Variable              | Default                         | Description |
|-----------------------|---------------------------------|-------------|
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1`  | Model provider endpoint. The **key** is saved per user in the UI, not here. |

## Network and access

This is the section you tune to access from **another machine** (LAN or VPS).

| Variable              | Default                  | Description |
|-----------------------|--------------------------|-------------|
| `WEB_ORIGIN`          | `http://localhost:3000`  | Frontend origin(s) accepted by **CORS**, comma-separated. In production, **only** these are accepted — include the one you'll open in the browser. |
| `NEXT_PUBLIC_API_URL` | (empty)                  | The backend URL the **browser** uses, baked into the build. **Leave it empty** so the frontend derives the API from the page host (works over localhost/LAN/VPS without a rebuild). Only set it if the backend has its own domain — and then **rebuild the web**. |
| `TRUST_PROXY`         | `false`                  | Enable **only** behind a trusted reverse proxy, so rate-limiting uses `X-Forwarded-For`. Without a proxy the header is forgeable. |

### Bind and ports

All published on `0.0.0.0` by default (except the opt-in ones, on `127.0.0.1`). If a port is
already taken, change the matching one — everything works the same, only where you reach it
changes.

| Variable        | Default   | Service            |
|-----------------|-----------|--------------------|
| `WEB_BIND` / `WEB_PORT`       | `0.0.0.0` / `3000` | Web interface |
| `SERVER_BIND` / `SERVER_PORT` | `0.0.0.0` / `8000` | API |
| `SEARXNG_PORT`   | `8080`   | `search` profile   |
| `EVOLUTION_PORT` | `8081`   | `whatsapp` profile |
| `KOKORO_PORT`    | `8880`   | `voice` profile    |
| `BROWSER_PORT`   | `3009`   | `browser` profile  |

## Web search

| Variable                | Default      | Description |
|-------------------------|--------------|-------------|
| `WEB_SEARCH_PROVIDER`   | `duckduckgo` | `duckduckgo` (no key) · `searxng` · `tavily` · `brave`. Tavily/Brave keys are per user. |
| `SEARXNG_URL`           | `http://searxng:8080` | Only change it to point at an external SearXNG (needs the JSON format enabled). |
| `WEB_SEARCH_MAX_RESULTS`| `5`          | Maximum results per search. |

## Voice (TTS/STT)

| Variable         | Default                     | Description |
|------------------|-----------------------------|-------------|
| `VOICE_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. Can point at local Kokoro (`http://kokoro:8880/v1`) or another server. |
| `TTS_MODEL`      | `tts-1`                     | Synthesis model. |
| `TTS_VOICE`      | `alloy`                     | Default voice. |
| `STT_MODEL`      | `whisper-1`                 | Transcription model. |

## Opt-in integrations

| Variable                 | Description |
|--------------------------|-------------|
| `EVOLUTION_API_KEY`      | Key for the Evolution service (unofficial WhatsApp). Generate a strong one. |
| `WHATSAPP_WEBHOOK_BASE`  | Public https URL of the server for the Meta **official Cloud API** webhooks. |
| `BROWSER_WS_URL` / `BROWSER_TOKEN` | CDP endpoint and token of the `browser` service (headless browser). |
| `TRANSCRIBE_COOKIES_DIR` | Folder with `cookies.txt` files (Netscape) rotated by video transcription, to reduce blocking. Empty = realistic headers only. |

## Observability

| Variable              | Default | Description |
|-----------------------|---------|-------------|
| `OBS_ENABLED`         | `true`  | Enables/disables trace recording. |
| `OBS_SAMPLE_RATE`     | `1.0`   | Sampled fraction [0..1]. Errors and slow traces are always kept. |
| `OBS_RETENTION_DAYS`  | `14`    | Retention (pruned by days). |
| `OBS_CAPTURE_CONTENT` | `false` | **Sensitive.** Records message/prompt text in spans — for debugging only. |

## Authentication / rate-limit

| Variable               | Default | Description |
|------------------------|---------|-------------|
| `ACCESS_TOKEN_TTL_MIN` | `30`    | Access token lifetime (minutes). |
| `REFRESH_TOKEN_TTL_DAYS`| `30`   | Refresh token lifetime (days). |
| `LOGIN_MAX_ATTEMPTS`   | `10`    | Login attempts per window. |
| `LOGIN_WINDOW_SECONDS` | `300`   | Login rate-limit window (seconds). |

## Tool sandbox

| Variable                    | Default | Description |
|-----------------------------|---------|-------------|
| `ALLOW_CODE_MODE`           | `true`  | Executes **model-generated** code in the sandbox (RCE by design, mitigated). Turn off in untrusted multi-user deployments. |
| `TOOL_TIMEOUT_SECONDS`      | `10`    | Wall-clock timeout of a tool. |
| `TOOL_CPU_SECONDS`          | `5`     | Subprocess CPU cap. |
| `TOOL_MEM_MB`               | `256`   | Subprocess memory cap. |
| `SIFT_CODE_TIMEOUT_SECONDS` | `30`    | Timeout of SIFT's `run_code`. |

> Check [`.env.example`](../.env.example) for the full list, including advanced variables not
> listed here.
</content>
