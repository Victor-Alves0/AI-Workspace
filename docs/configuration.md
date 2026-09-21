# Configuration

Three places hold configuration, and knowing which is which saves a lot of time:

| Where | What lives there |
|---|---|
| [`.env`](../.env.example) | Infrastructure decisions only: environment, master secret, database password, public domain, opt-in service keys. ~8 values. |
| **Inside the app** | Per-user API keys (OpenRouter, Tavily/Brave, voice, bots, OAuth), models, tools, search preference, budget, who may sign up. Encrypted at rest in the database. |
| **Defaults in the code** | Everything else — limits, timeouts, context windows, observability. Listed below; change one only when you have a reason. |

```bash
cp .env.example .env
```

> **Per-user secrets never go in `.env`.** Each user saves their own in the interface and they
> are encrypted with a key derived from `APP_SECRET`. Only infrastructure secrets live in `.env`.

## What `.env` holds

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | `production` refuses to start with a weak `APP_SECRET` and stops accepting HTTP origins. |
| `APP_SECRET` | — | **Must be changed.** Signs the JWTs and derives the encryption key for the secrets stored in the database. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `POSTGRES_PASSWORD` | `aiworkspace` | Change it for a real install. `POSTGRES_USER` / `POSTGRES_DB` exist too and rarely need changing. |
| `CADDY_SITE` | `:443` | Address the HTTPS proxy answers on. The default answers on **any** host/IP. |
| `CADDY_MODE` | `local` | `local` = internal CA, certificate issued on demand for whatever name/IP is used. `public` = Let's Encrypt for the domain in `CADDY_SITE`. See [https.md](https.md). |
| `WEB_ORIGIN` | `https://localhost` | Extra origins accepted by CORS. Only needed for a **public domain** — the proxy puts frontend and API on the same origin, and any private-network IP over https is already accepted. |
| `HTTPS_PORT` / `HTTP_PORT` | `443` / `80` | Change when the host already uses those ports. |
| `EVOLUTION_API_KEY` | — | Shared key of the `whatsapp` profile (Evolution service). |
| `BROWSER_TOKEN` | — | Shared token of the `browser` profile (headless Chromium over CDP). |

> **`APP_SECRET` does not grant database access** (that's the Postgres password) — it encrypts
> the secret **values**. Changing it carelessly makes them unreadable (the app treats them as
> "not configured") and logs everyone out. To change it without losing anything, use the
> rotation procedure in [deployment.md](deployment.md#rotating-the-app_secret).

### Secrets by file (server profile)

Passed through the environment, `APP_SECRET` is readable in `/proc/1/environ` by **any** command
the AI runs inside the container — and `APP_SECRET` + `DATABASE_URL` decrypts every user's
tokens. Same secret, safer delivery:

```bash
mkdir -p secrets && printf '%s' "$APP_SECRET" > secrets/app_secret && chmod 600 secrets/app_secret
# uncomment the ./secrets mount in docker-compose.yml (server service)
# leave APP_SECRET empty in .env and set:
#   APP_SECRET_FILE=/run/secrets/app_secret
#   DATABASE_URL_FILE=/run/secrets/database_url
```

Admin → Health warns while secrets still come from the environment. See
[trust-model.md](trust-model.md).

## Configured in the app, not in `.env`

- **Sign-ups** — the first account is always allowed and becomes admin; after that registration
  stays closed until the admin opens it in the panel. There is no `ENABLE_SIGNUP`.
- **Provider keys** (OpenRouter, Tavily, Brave, voice, ElevenLabs, bots, OAuth client id/secret)
  — Settings → Connections / Integrations, per user, encrypted.
- **Search provider** — the per-user preference wins over `WEB_SEARCH_PROVIDER`.
- **Models, tools, budget, memory, shortcuts** — panels in the app.

## Everything else

These have working defaults. They are kept out of `.env.example` to keep that file short, but
they still work from `.env` — with one exception, marked **override-only** below.

Under Docker the server only receives what `docker-compose.yml` forwards to it. Addresses,
ports and everything in the sections *Database and network*, *Providers*, *Integrations* and
*Video transcription* are forwarded, so `.env` is enough. The tuning knobs (session, agent loop,
sandbox, Codespace, attachments, observability) are not — putting them in `.env` changes nothing
under Docker. Set those in `docker-compose.override.yml`, which Compose merges automatically and
git ignores:

```yaml
services:
  server:
    environment:
      MAX_TOOL_ITERATIONS: 12
      UPLOAD_MAX_BYTES: 1073741824
```

Outside Docker (`uvicorn`, desktop build) `.env` is read directly and every variable works.

### Database and network

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | assembled by compose | Only touch it to run the server outside Docker. |
| `DB_PORT` / `DB_BIND` | `5432` / `127.0.0.1` | Postgres is **not** published on the host. To reach it with psql/DBeaver, copy `docker-compose.override.yml.example`. |
| `WEB_BIND` / `WEB_PORT` | `127.0.0.1` / `41414` | Frontend in plain HTTP. Loopback by default: the proxy is what serves the network. 41414 is AI Workspace's own port — 3000 is almost always taken by some dev server, and a taken host port makes `docker compose up` fail. |
| `SERVER_BIND` / `SERVER_PORT` | `127.0.0.1` / `8000` | API in plain HTTP, same reasoning. |
| `TRUST_PROXY` | `true` in compose | Makes the server trust `X-Forwarded-*` (real client IP, http/https). Turn it off if you publish port 8000 straight to the internet. |
| `NEXT_PUBLIC_API_URL` | empty | Backend URL baked into the web build. **Leave it empty** — the frontend derives the API from the page origin, so one build serves localhost, LAN and VPS. |
| `SEARXNG_PORT` · `EVOLUTION_PORT` · `KOKORO_PORT` · `BROWSER_PORT` | `8080` · `8081` · `8880` · `3009` | Host ports of the opt-in profiles. |
| `CHATGPT_OAUTH_BIND` / `CHATGPT_OAUTH_PORT` | `127.0.0.1` / `1455` | Fixed callback of the ChatGPT/Codex login. |
| `PREVIEW_BIND` / `PREVIEW_PORT_MIN` / `PREVIEW_PORT_MAX` | `127.0.0.1` / `4001` / `4010` | Codespace own-origin previews. **Unauthenticated ports** — keep them on loopback and use `/codespace/preview/<port>/` from other devices. |

### Providers

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Model provider endpoint. |
| `OPENROUTER_APP_NAME` / `OPENROUTER_APP_URL` | `AI Workspace` / `https://ai-workspace.app` | Identification shown in the OpenRouter dashboard. *(override-only)* |
| `WEB_SEARCH_PROVIDER` | `duckduckgo` | Fallback when the user has no preference: `duckduckgo` · `searxng` · `tavily` · `brave`. |
| `SEARXNG_URL` | `http://searxng:8080` | Only for an external SearXNG (needs the JSON format enabled). |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Results per search. *(override-only)* |
| `VOICE_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible speech endpoint. Local voice is configured per user in the app. |
| `TTS_MODEL` / `TTS_VOICE` / `STT_MODEL` | `tts-1` / `alloy` / `whisper-1` | Defaults when the user picked nothing. *(override-only)* |
| `SIFT_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedder of SIFT tool discovery. A multilingual model (`intfloat/multilingual-e5-large`, ~2.2 GB) auto-invalidates the cached indexes. |

### Integrations (OAuth redirect URIs)

Client id/secret live in the app. Only the redirect URI is an env var, and it must match what is
registered in each provider's console — on a public domain all five change. See
[integration-auth.md](integration-auth.md).

| Variable | Default |
|---|---|
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/integrations/google/callback` |
| `GITHUB_REDIRECT_URI` | `http://localhost:8000/integrations/github/callback` |
| `NOTION_REDIRECT_URI` | `http://localhost:8000/integrations/notion/callback` |
| `SLACK_REDIRECT_URI` | `http://localhost:8000/integrations/slack/callback` |
| `OPENROUTER_REDIRECT_URI` | `http://localhost:8000/integrations/providers/openrouter/callback` |
| `GITHUB_DEVICE_CLIENT_ID` | empty — public client id of "Sign in with GitHub" (device flow); empty falls back to a Personal Access Token |
| `BROWSER_WS_URL` | `ws://browser:3000` in compose — CDP endpoint of the headless browser; `local` drives the Edge/Chrome installed on the machine (the desktop app uses this); empty disables the tool |
| `EVOLUTION_API_URL` | empty = the compose service; set it for a remote Evolution instance |
| `WHATSAPP_WEBHOOK_BASE` | `http://server:8000` — public https URL for Meta's official Cloud API webhooks |

### Session and login *(override-only)*

| Variable | Default | Description |
|---|---|---|
| `ACCESS_TOKEN_TTL_MIN` | `30` | Access token lifetime. |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | Refresh token lifetime. |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_SECONDS` | `10` / `300` | Login rate limit. |

### Agent loop and context *(override-only)*

| Variable | Default | Description |
|---|---|---|
| `MAX_TOOL_ITERATIONS` | `8` | Tool rounds allowed in one turn. |
| `CODESPACE_MAX_TOOL_ITERATIONS` | `150` | Same for a Codespace chat, which runs write → test → fix. |
| `AGENT_NOPROGRESS_REPEATS` | `3` | Same (tool, args, result) repeated this many times forces an answer. |
| `TOOL_RESULT_CONTEXT_BUDGET_CHARS` | `60000` | Old tool results are shrunk in the resent prompt (the full result stays saved). `0` disables. |
| `TOOL_RESULT_CONTEXT_KEEP_LAST` / `TOOL_RESULT_CONTEXT_EXCERPT_CHARS` | `2` / `4000` | How many stay whole and how much of the rest survives. |
| `AUTOCOMPACT_ENABLED` | `true` | Old history becomes a summary past the window threshold. |
| `AUTOCOMPACT_THRESHOLD` / `AUTOCOMPACT_KEEP_LAST` / `AUTOCOMPACT_MIN_MESSAGES` | `0.75` / `8` / `12` | When it triggers and what stays whole. |
| `AUTOCOMPACT_FALLBACK_WINDOW` | `100000` | Window assumed when the provider does not report one. |
| `PROMPT_CACHE_ENABLED` | `true` | OpenRouter prompt caching. |
| `MAX_MESSAGE_CHARS` / `MAX_TOOL_CODE_CHARS` | `100000` / `50000` | Size ceiling of a message and of model-generated code. |
| `SIFT_INDEX_CACHE_DIR` | `/home/app/.cache/sift-index` | `.npz` index cache; empty disables it. |

### Tool sandbox *(override-only)*

| Variable | Default | Description |
|---|---|---|
| `ALLOW_CODE_MODE` | `true` | Runs **model-generated** code in a subprocess sandbox (RCE by design: CPU/memory/time are capped, network and `/proc` are not). Turn it off in untrusted multi-user deployments. |
| `SIFT_CODE_TIMEOUT_SECONDS` / `SIFT_CODE_MEM_MB` | `30` / `512` | Limits of that sandbox. |
| `TOOL_TIMEOUT_SECONDS` / `TOOL_CPU_SECONDS` / `TOOL_MEM_MB` | `10` / `5` / `256` | Limits of one native tool call. |
| `BUILTIN_TOOL_TIMEOUT_SECONDS` | `120` | Wall-clock ceiling per tool call: a stuck tool returns an error instead of hanging the turn. `0` = no ceiling. |

### Codespace *(override-only)*

| Variable | Default | Description |
|---|---|---|
| `CODESPACE_DATA_DIR` | `/data/codespace` | Where project working copies live. |
| `CODE_RUNNER_URL` / `CODE_RUNNER_TOKEN` | empty | Optional container runner for `code.exec.run`; empty runs on the host. |
| `CODE_EXEC_TIMEOUT_SECONDS` / `CODE_EXEC_CPU_SECONDS` / `CODE_EXEC_OUTPUT_BYTES` | `900` / `1800` / `200000` | Limits of a foreground command. |
| `CODE_EXEC_BG_WAIT_CEILING_SECONDS` | `1200` | How long the agent waits inline on a background command before releasing the turn (it is woken up at the end). |
| `CODE_EXEC_BG_CPU_SECONDS` / `CODE_EXEC_BG_MAX_SECONDS` | `0` / `7200` | CPU (`0` = unlimited, a long build is the use case) and the watchdog that kills it. |
| `CODESPACE_WORKTREE_TTL_SECONDS` / `CODESPACE_MAX_WORKTREES_PER_USER` | `86400` / `20` | Worktree lifecycle. |
| `CODE_PREVIEW_MAX_AGE_SECONDS` / `CODE_PREVIEW_MAX_PER_USER` | `21600` / `4` | Live preview lifecycle. |
| `CODE_PREVIEW_PORT_MIN` / `CODE_PREVIEW_PORT_MAX` | `4001` / `4010` | Must match the range published in compose. |

### Attachments *(override-only)*

The file uploads on its own (`/uploads`, written to disk in chunks) and the message keeps only a
reference — which is why a large document fits. Images and audio have a smaller ceiling because
they travel whole in the request to the provider.

| Variable | Default | Description |
|---|---|---|
| `UPLOADS_DIR` | `/data/uploads` | Storage path (a named volume in compose). |
| `UPLOAD_MAX_BYTES` | `500 MB` | Per file. |
| `UPLOAD_IMAGE_MAX_BYTES` / `UPLOAD_AUDIO_MAX_BYTES` | `20 MB` / `25 MB` | Per image / per audio. |
| `UPLOAD_MAX_PER_MESSAGE` | `20` | Attachments in one message. |
| `UPLOAD_QUOTA_BYTES` | `25 GB` | Per user. |
| `UPLOAD_ORPHAN_TTL_HOURS` | `24` | Deletes what was uploaded and never became a message. |
| `UPLOAD_TEXT_MAX_CHARS` | `2000000` | Ceiling of the text extracted from one document. |
| `MAX_JSON_BODY_BYTES` | `32 MB` | JSON body ceiling — without it a huge POST was read into memory until the process died. |

### Video transcription

| Variable | Default | Description |
|---|---|---|
| `TRANSCRIBE_COOKIES_DIR` | empty | Folder of `cookies.txt` files (Netscape); each file is an identity the pool rotates and cools down when blocked. Empty = realistic headers only. |
| `TRANSCRIBE_COOKIE_COOLDOWN_SECONDS` | `1800` | Cooldown after a block. |

### Observability *(override-only)*

| Variable | Default | Description |
|---|---|---|
| `OBS_ENABLED` | `true` | End-to-end traces in Postgres. |
| `OBS_SAMPLE_RATE` | `1.0` | Sampled fraction; errors and slow traces are always kept. |
| `OBS_RETENTION_DAYS` / `OBS_MAX_TRACES` | `14` / `500000` | Retention. |
| `OBS_SLOW_MS` | `1500` | What counts as slow. |
| `OBS_CAPTURE_CONTENT` | `false` | **Sensitive.** Records message/prompt text in spans — debugging only. |
| `GIT_COMMIT` | empty | Commit the image was built from; the CI injects it as a build arg (not override-only) and the Health panel shows it. |
