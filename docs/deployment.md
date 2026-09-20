# Deployment and operations

How to get AI Workspace running beyond `localhost`: local network, VPS, domain with HTTPS, plus
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

Three lines is the whole file for an IP-based install:

```dotenv
APP_ENV=production
APP_SECRET=<the generated value>
POSTGRES_PASSWORD=<a strong password>
```

No origin to declare: the proxy serves frontend and API on the **same** address, and any
private-network IP over https is accepted. A public domain adds three more lines — see
[Domain + HTTPS](#domain--https).

### 2. Bring it up

```bash
docker compose up -d --build
```

### 3. Open the firewall ports

The app is reached through the proxy, on **443** (and **80**, which redirects and is what
Let's Encrypt validates against):

```bash
# ufw (Ubuntu/Debian)
sudo ufw allow 443/tcp
sudo ufw allow 80/tcp
```

> On AWS/GCP/Oracle, also open 443 and 80 in the **Security Group** in the console.

Visit **https://YOUR_IP**, register (you become admin) and paste the OpenRouter key. The first
visit warns about the certificate (internal CA) until you trust it — [https.md](https.md).

> Ports **3000** and **8000** stay on loopback: they are the plain-HTTP way in, kept for local
> debugging. To publish them anyway, `WEB_BIND=0.0.0.0` / `SERVER_BIND=0.0.0.0`.

## Domain + HTTPS

**HTTPS already comes with the stack**: the `proxy` service (Caddy) serves the front at
`https://<host>/` and the API at `https://<host>/api` — one address for both, so there is no
CORS in the way and the session cookie covers the whole address. Full guide:
[https.md](https.md).

- **LAN/VPN/IP:** nothing to configure. The certificate comes from Caddy's internal CA; trust
  it once (see the guide) and the browser warning goes away.
- **Public domain:** `CADDY_SITE=app.your-domain.com`, `CADDY_MODE=public` (Let's Encrypt,
  renewed automatically) and `WEB_ORIGIN=https://app.your-domain.com`.
- Ports 80/443 busy? `HTTP_PORT`/`HTTPS_PORT`.
- `TRUST_PROXY` is `true` by default so rate-limiting and the IP allowlist see the real client
  via `X-Forwarded-For`. Set it to `false` if you publish port 8000 straight to the internet.
- You can stop publishing the app ports entirely and let only the proxy reach them:
  `SERVER_BIND=127.0.0.1` and `WEB_BIND=127.0.0.1`.

`NEXT_PUBLIC_API_URL` stays **empty** in this setup: through the proxy the front calls `/api` on
its own origin, and on a direct visit to port 3000 it still calls `host:8000`. Only set it when
the backend has a domain of its own (`https://api.your-domain.com`) — that one is baked into the
build, so rebuild the web afterwards.

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

**Restore** — the admin panel does it for you (Admin → Backup); the equivalent by hand is two
steps, and **never** `pg_restore --clean` straight onto the live database. `--clean` only drops
what the dump knows about, so an older backup leaves the newer tables in place, the drops fail on
their foreign keys and the database ends up half old, half erased:

```bash
docker compose stop server
docker compose up -d db

# 1) dump → SQL script. A corrupt dump fails HERE, before the database is touched.
docker compose exec -T db sh -c \
  'cat > /tmp/b.dump && pg_restore --no-owner --no-privileges -f /tmp/r.sql /tmp/b.dump' < backup.dump

# 2) empty the schema and apply the script in ONE transaction: any error rolls everything back
docker compose exec -T db sh -c \
  'printf "DROP SCHEMA IF EXISTS public CASCADE;\nCREATE SCHEMA public;\n" > /tmp/p.sql \
   && psql -U aiworkspace -d aiworkspace --single-transaction --set=ON_ERROR_STOP=1 -f /tmp/p.sql -f /tmp/r.sql \
   && rm -f /tmp/b.dump /tmp/r.sql /tmp/p.sql'

docker compose up -d   # startup migrations bring the schema back to head
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
- [ ] `WEB_ORIGIN` only if you use a public domain (an IP needs nothing).
- [ ] Sign-ups closed in the admin panel after creating your account (they start closed).
- [ ] Reached over HTTPS through the `proxy` service, with `TRUST_PROXY=true`.
- [ ] Firewall/Security Group opening only 443/80.
- [ ] A scheduled backup routine (`pg_dump`).
- [ ] Consider `ALLOW_CODE_MODE=false` if there are untrusted users.
</content>
