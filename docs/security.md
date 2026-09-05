# Security

AI Workspace stores sensitive secrets (API keys, OAuth/bot tokens) and can execute
model-generated code. This page describes the security model and the operational recommendations.

## Authentication and session

- **Passwords** with **Argon2** (not plain SHA/bcrypt).
- **Session** via **JWT in an httpOnly cookie** — a short access token (~30 min) + a rotating
  refresh token (days). The frontend renews on its own on a `401`.
- **Global revocation** via `token_version`: a bump invalidates all of a user's sessions.
- Optional **2FA (TOTP)**, with a QR and a login challenge.
- **RBAC** admin/user. The first user to register becomes admin; close registration with
  `ENABLE_SIGNUP=false`.

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

## Tool execution (sandbox)

The AI can write and execute **Python code** (the highest-risk vector — RCE by design).
Mitigations:

- Code runs in an **isolated subprocess** (`python -I`), **not** in the server process.
- **CPU**, **memory** and **wall-clock** limits (`TOOL_*`, `SIFT_CODE_TIMEOUT_SECONDS`).
- **Anti-SSRF guard** on the browsing/page-reading tools (blocks internal IPs, no `file://`).

> ⚠️ The sandbox limits CPU/memory/time, but does **not** isolate the network or `/proc`. In an
> **untrusted multi-user** deployment, consider turning off `ALLOW_CODE_MODE`, or hardening the
> sandbox (network off, `hidepid`, nsjail/gVisor) and delivering secrets by file. In a
> **single-user self-hosted** install (the common case), the risk is your own code.

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
- `ENABLE_SIGNUP=false` after creating your account.
- Regular backups (see [deployment.md](deployment.md#backup-and-restore)).
- Observability: `OBS_CAPTURE_CONTENT` is **off** by default (it does not record message/prompt
  text) — only enable it to debug.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
</content>
