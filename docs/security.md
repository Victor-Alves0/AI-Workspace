# Security

AI Workspace stores sensitive secrets (API keys, OAuth/bot tokens) and can execute
model-generated code. This page describes the security model and the operational recommendations.

## Authentication and session

- **Passwords** with **Argon2** (not plain SHA/bcrypt).
- **Session** via **JWT in an httpOnly cookie** — a short access token (~30 min) + a rotating
  refresh token (days). The frontend renews on its own on a `401`.
- **Global revocation** via `token_version`: a bump invalidates all of a user's sessions.
- Optional **2FA (TOTP)**, with a QR and a login challenge.
- **RBAC** admin/user. A fresh install opens a **first-run wizard** that creates the admin and
  asks whether others may sign up; registration is closed by default and can be opened later in
  **Admin**. New accounts wait for the admin's approval.

## Secrets at rest

- **Per-user** secrets are encrypted with **Fernet**, with a key **derived from `APP_SECRET`** —
  they are never written in plaintext to the database.
- `APP_SECRET` does **not** grant database access (that's the Postgres password); it encrypts the
  **values**.
- There's a **rotation** tool that re-encrypts the entire database when you change `APP_SECRET`,
  without losing the secrets (see [deployment.md](deployment.md#rotating-the-app_secret)).
- `.env` (which holds `APP_SECRET` and the Postgres password) must **never** be committed — it's
  already in `.gitignore`.

## Network and transport

- **CORS** restricted to the origins in `WEB_ORIGIN`. Under `APP_ENV=production`, **only** the
  exact origins are accepted (the LAN is not opened automatically).
- **Security headers** on every response (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`) and **HSTS** under HTTPS.
- Optional **IP allowlist** at the server level.
- Behind a trusted reverse proxy, enable `TRUST_PROXY=true` so rate-limiting uses the real IP.
- Postgres is **not** published on the host by default — the app reaches it over the internal
  Compose network.

## Rate limiting

- **Login/registration**: `LOGIN_MAX_ATTEMPTS` per `LOGIN_WINDOW_SECONDS`.
- **Public API**: per key — RPM, RPD, monthly, tokens, concurrency and budget (see
  [public-api.md](public-api.md)).

## Code execution (isolated runner)

The AI runs code in four ways: Codespace commands, live previews, `run_code` (code mode), and
tools written by you or by the AI. All four are RCE by design, and the threat is prompt
injection (see [trust-model.md](trust-model.md)).

**On a server (Docker), none of it runs in the backend.** It runs in the `runner` container,
which has:

- **No secrets.** Nothing in its environment, no secret files, and no updater token.
- **Only the projects volume** (`/data/codespace`). No uploads, no WhatsApp sessions, no model
  cache.
- **No route to the database, updater or browser.** It sits on its own `sandbox` network,
  shared only with the server.
- **Tight container limits.** Read-only root filesystem, no Linux capabilities,
  `no-new-privileges`, and caps on CPU, memory and PIDs (`RUNNER_CPUS`, `RUNNER_MEMORY`,
  `RUNNER_PIDS`).
- **A bearer token** for every request. The token is created by the runner and read by the
  server from a shared volume.

The server keeps the credentials. In `run_code`, the snippet runs in the runner, but each
tool it calls runs back in the server, so the model's code never touches a token.

Two gaps close along with it:

- **Git.** The runner writes to the same folders the server runs `git` in. Before each call,
  the server strips repo config keys that name a program (hooks, fsmonitor, filters, pagers,
  diff/merge drivers, `sshCommand`, includes…). It also pushes to the project's registered
  URL, never to an `origin` someone could have rewritten.
- **Folders.** On a server, a chat or project folder must live inside the projects area. The
  rest of the backend container holds its secrets.

**If there is no runner in production, execution is off.** Commands, previews and `run_code`
refuse with an error that says how to turn them on, and the health check raises an alarm.

**On the desktop app,** code runs on your own machine, and access to it is the feature. CPU,
memory and wall-clock limits still apply (`TOOL_*`, `SIFT_CODE_TIMEOUT_SECONDS`).

The browsing and page-reading tools keep their **anti-SSRF guard**: internal IPs are blocked
and `file://` is refused.

**Internet for the runner** is on by default, because package managers need it. Its secrets
are not there to exfiltrate. For a runner with no network, set the `sandbox` network to
`internal: true` in a `docker-compose.override.yml`. Previews and installs will then stop
working.

**Actions without a person watching** follow the same logic. Automations, the API and open
channels only act in categories the owner allowed beforehand. The model's `confirm=true`
does not count as approval (see [trust-model.md](trust-model.md)).

## Remote Terminal (your own machines)

The Remote Terminal tool gives the AI a **real shell on a machine you own**, through an
agent you install there. It is the widest capability in the product, so the defaults are
deliberately narrow:

- The agent authenticates every call with a **per-machine bearer token** (constant-time
  compare) over TLS; the workspace pins the agent's self-signed certificate by default.
- Commands run as a **dedicated non-root user**, not as whoever installed the agent.
- **Confirmation is on by default per machine** — unlike the Codespace sandbox, the default
  here is to ask.
- Outbound traffic can be **sealed through a proxy** on both legs — the workspace→machine
  connection and the machine's own command egress — each with a killswitch that **refuses
  rather than falling back to the direct route**. A fallback would leak exactly the address
  the proxy exists to hide, silently.

Two limits worth knowing before you enable it: the sealed mode filters by **uid**, so `sudo`
(uid 0) escapes it — the installer refuses `--grant-sudo` together with `--force-egress`;
and a machine reachable from the internet should have its agent port restricted (firewall,
`--bind 127.0.0.1` behind a tunnel, or `allow_cidrs`).

See [remote-terminal.md](remote-terminal.md).

## Production hardening

- `APP_ENV=production` makes the server **refuse to start** with a weak/short `APP_SECRET`.
- HTTPS with a reverse proxy + `TRUST_PROXY=true`.
- Keep registration **closed** (the wizard's default) unless you really want others signing up.
- Regular backups (see [deployment.md](deployment.md#backup-and-restore)).
- Observability: `OBS_CAPTURE_CONTENT` is **off** by default (it does not record message/prompt
  text) — only enable it to debug.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
</content>
