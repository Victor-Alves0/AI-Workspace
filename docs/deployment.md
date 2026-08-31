# Deployment and operations

How to get Singularity AI running beyond `localhost`: local network, VPS, domain with HTTPS, plus
updates, backup/restore and secret rotation.

## Table of contents

- [Local network / VPS](#local-network--vps)
- [Domain + HTTPS](#domain--https)
- [Updating](#updating)
- [Backup and restore](#backup-and-restore)
- [Rotating the APP_SECRET](#rotating-the-app_secret)

## Local network / VPS

The app is built for this: the frontend **discovers the backend from the page host**, so the
same build works over `localhost`, the LAN IP and the VPS — **without a rebuild**. What changes
is **CORS** and the **firewall**.

### 1. Prepare `.env`

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into APP_SECRET
```

Adjust:

```dotenv
APP_ENV=production
APP_SECRET=<the generated value>
POSTGRES_PASSWORD=<a strong password>

# The origin you'll open in the browser. In production CORS accepts ONLY what's
# here. Use the VPS IP or your domain, with port 3000:
WEB_ORIGIN=http://YOUR_IP_OR_DOMAIN:3000

# Leave EMPTY: the browser calls the backend on the same host, port 8000.
NEXT_PUBLIC_API_URL=
```

> Going to use it from several addresses (e.g. localhost **and** the IP)? List them
> comma-separated: `WEB_ORIGIN=http://localhost:3000,http://YOUR_IP:3000`

### 2. Bring it up

```bash
docker compose up -d --build
```

### 3. Open the firewall ports

The app publishes **3000** (web) and **8000** (server):

```bash
# ufw (Ubuntu/Debian)
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
```

> On AWS/GCP/Oracle, also open 3000 and 8000 in the **Security Group** in the console.

Visit **http://YOUR_IP:3000**, register (you become admin) and paste the OpenRouter key.

## Domain + HTTPS

For real production, put a **reverse proxy** (Caddy/nginx/Traefik) in front, terminating TLS,
pointing `/` to `web:3000` and (if you prefer to split it) a dedicated URL to `server:8000`. In
that case:

- `WEB_ORIGIN=https://your-domain.com`
- If the backend has its own domain, set `NEXT_PUBLIC_API_URL=https://api.your-domain.com`
  **and rebuild the web** (`docker compose up -d --build web`) — that URL is baked into the build.
- Enable `TRUST_PROXY=true` so rate-limiting sees the real IP via `X-Forwarded-For`.
- You can even skip publishing the server port: let only the proxy reach it with
  `SERVER_BIND=127.0.0.1`.

Minimal example with **Caddy** (automatic TLS):

```caddy
your-domain.com {
    reverse_proxy localhost:3000
}
api.your-domain.com {
    reverse_proxy localhost:8000
}
```

## Updating

The recommended way is the script at the root, which validates, rebuilds and migrates:

```bash
./update.sh
```

It: checks there are no local changes, does `git pull --ff-only`, rebuilds the images, brings
the containers up and runs `alembic upgrade head`. Manually that would be:

```bash
git pull
docker compose up -d --build
```

> **Database migrations run automatically** on server startup; the explicit step in `update.sh`
> is just a safeguard. The admin panel warns when a new version is available.

## Backup and restore

All state lives in Postgres (data + vectors + embeddings). A single `pg_dump` covers everything.

**Backup:**

```bash
docker compose exec -T db pg_dump -U aiworkspace -Fc aiworkspace > backup.dump
```

**Restore** (into a fresh/clean environment):

```bash
docker compose up -d db
docker compose exec -T db pg_restore -U aiworkspace -d aiworkspace --clean --if-exists < backup.dump
docker compose up -d
```

> **To migrate to another machine, carry the same `APP_SECRET`.** Per-user secrets are encrypted
> with a key derived from it; with a different `APP_SECRET` the database restores but the secrets
> are unreadable (the app treats them as "not configured"). The admin panel also offers
> export/restore from there.

## Rotating the `APP_SECRET`

Need to change `APP_SECRET` (leaked, policy, etc.) **without** losing the stored secrets? There's
a tool that re-encrypts everything from the old key to the new one. Run it in dry-run mode first:

```bash
docker compose exec -e NEW_APP_SECRET=<new> server \
  python -m aiworkspace.secret_rotation --dry-run

docker compose exec -e NEW_APP_SECRET=<new> server \
  python -m aiworkspace.secret_rotation
```

Then set `APP_SECRET=<new>` in `.env` and restart:

```bash
docker compose up -d server
```

## Production checklist

- [ ] `APP_ENV=production` and a strong `APP_SECRET` (generated, not the default).
- [ ] `POSTGRES_PASSWORD` changed.
- [ ] `WEB_ORIGIN` with the real origin(s); `NEXT_PUBLIC_API_URL` empty (or the API domain).
- [ ] `ENABLE_SIGNUP=false` after creating your account.
- [ ] Reverse proxy with HTTPS and `TRUST_PROXY=true`.
- [ ] Firewall/Security Group opening only what's needed.
- [ ] A scheduled backup routine (`pg_dump`).
- [ ] Consider `ALLOW_CODE_MODE=false` if there are untrusted users.
</content>
