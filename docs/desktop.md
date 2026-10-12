# Desktop app (Windows)

A native app that opens AI Workspace in its own window, with a **tray icon**, **"run in the
background"** and **"start with Windows"**. Built with [Tauri](https://tauri.app/).

## What it adds

The window loads the **same web interface** you use in the browser (at
`http://localhost:41414`), so behavior is identical — what the shell adds is what a browser
doesn't give you:

| Feature | Where to configure |
|---|---|
| Closing hides to the tray (instead of quitting) | Settings → App, or the tray menu |
| Start with Windows | same |
| Start minimized when launched with Windows | same |
| Server address (point at another machine) | same |

> **Why load the web UI and not local assets?** The session is an `httpOnly` cookie issued by
> the API; serving the interface from a `tauri://` origin would make every call cross-origin and
> login wouldn't stick. By pointing at the real origin, the CORS and cookies that already work
> keep working.

## One process, one port

The engine runs **Postgres + one Python process**. That process
(`aiworkspace.desktop`) serves both halves on the same port, the same shape the Caddy proxy
gives the Docker install:

| Path | Serves |
|---|---|
| `/api/*` | the API (the prefix is stripped before it reaches the API, exactly like Caddy's `handle_path`) |
| `/auth/callback` | the ChatGPT/Codex login callback |
| everything else | the interface, exported as static files (`NEXT_OUTPUT=export`) |

There is no Node in the engine: the interface uses no server features (no API routes,
middleware, server actions or `cookies()`/`headers()`), so the static export plus this
dispatcher replaces the whole `web` container. A repository invariant keeps it that way.

**Port 41414** is AI Workspace's own port — 3000 and 8000 are almost always taken by some dev
server. If 41414 is busy (or reserved by Windows: Hyper-V/WSL reserve whole blocks), the
launcher takes the next free one up to 41433 and writes it to `port.txt` in the data folder;
the shell reads it from there and only navigates once `/api/health` answers — a stale file
pointing at some other program's port is never followed.

The OpenAI login always returns to `localhost:1455`; in Docker that port is published, here the
app forwards it to its own port. If 1455 is taken, only that fallback flow is lost (the device
code flow keeps working).

**OAuth redirect URIs** (Google, GitHub, Notion, Slack) become
`http://localhost:41414/api/integrations/<service>/callback` — register that in each
provider's console. If the launcher had to fall back to another port, the URI changes with it.

## Preferences are per-machine

The options live in `%APPDATA%\com.aiworkspace.app\desktop-settings.json` — **not** in the
user's profile in the database. Storing them on the server would make the phone show "start with
Windows", and two PCs on the same account would fight over the value. That's why the **App**
category only appears in Settings when the UI runs inside the installed app.

## Install

The installer is published on **[Releases](../../releases)** (built by CI, not versioned in the
repository). It **embeds the whole engine** — Postgres, the Python backend (which also serves the
static interface) and the embeddings model — so there is no Docker to run: install, open, and it
boots the stack itself.

## Updates (two layers)

Reinstalling ~1 GB for a small code change would be painful, so updates are split in two:

- **Engine** (heavy: Python, dependencies, Postgres, model) — versioned by
  `desktop/engine/ENGINE_VERSION`. Rarely changes; a new value ships in a **full installer**.
- **App code** (light: the `aiworkspace` backend package, the `web` frontend, the migrations) —
  changes every commit. Published on its own to the rolling **`app-latest`** release
  (`app-<sha>.zip` + `app-update.json`) by the **App code update** workflow.

On every launch, before starting the servers, the launcher reads the manifest and — if a newer
`app_version` is published **and** the installed engine satisfies the manifest's `engine_required`
— downloads only the code zip, verifies its SHA-256, and mirrors those folders in place. Any
failure (offline, bad checksum) is ignored and the app boots the code already installed. Opt out
with the env var `AIW_NO_UPDATE=1` or a `.no-update` file in the data dir.

**Publishing a code-only update:** run the *App code update (Windows)* workflow. **Changing a
Python dependency:** bump `ENGINE_VERSION`, ship a new full installer, then publish the code update
(its `engine_required` now points at the new engine, so old installs correctly wait for the `.exe`).

## Build

CI (`windows-latest`, workflow `.github/workflows/desktop.yml`) already has everything. To build
locally you need:

- **Rust** (https://rustup.rs) and **Node 20+**
- **Visual Studio Build Tools** with "Desktop development with C++" — the
  `x86_64-pc-windows-msvc` toolchain needs `link.exe` and the Windows SDK (which is why the
  official build runs on CI).
- **WebView2 Runtime** — already included on an up-to-date Windows 10/11.

```bash
cd desktop
npm install
npm run build     # installer in src-tauri/target/release/bundle/nsis/
```

Publishing a release triggers CI:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## Code signing (self-signed)

The installer is signed in CI with a **self-signed** code-signing certificate so the
binary carries a publisher identity instead of "Unknown publisher". CI imports the cert
(from the `WINDOWS_CERT_PFX_BASE64` / `WINDOWS_CERT_PASSWORD` repo secrets) and Tauri
signs the binary + NSIS installer using the `certificateThumbprint` in
[`tauri.conf.json`](../desktop/src-tauri/tauri.conf.json).

A self-signed signature is **not** trusted by other machines out of the box — SmartScreen
still warns until each machine trusts the certificate once. To establish trust, install the
public certificate ([`desktop/aiworkspace-codesign.cer`](../desktop/aiworkspace-codesign.cer))
into **Trusted Root Certification Authorities** (and optionally **Trusted Publishers**):

```powershell
# per-machine (needs admin) — makes the signature trusted for all users
Import-Certificate -FilePath aiworkspace-codesign.cer `
  -CertStoreLocation Cert:\LocalMachine\Root
```

Or double-click the `.cer` → **Install Certificate** → *Local Machine* → *Place all
certificates in the following store* → **Trusted Root Certification Authorities**.

For public distribution to strangers (no manual trust step), move to a CA-issued OV/EV
certificate — e.g. **Azure Trusted Signing** — and swap the thumbprint/import step; the rest
of the pipeline stays the same.

## Linux (preview)

The same shell and the same self-contained engine, built for Linux (x86_64) as an
**AppImage** and a **.deb** by `.github/workflows/desktop-linux.yml`. Tray, "run in the
background" and "start with the system" (a `.desktop` file in `~/.config/autostart`) work as
on Windows; on GNOME the tray needs the AppIndicator extension.

What differs from Windows, and why:

| | Windows | Linux |
|---|---|---|
| Python | python.org *embeddable* | [python-build-standalone](https://github.com/astral-sh/python-build-standalone) (relocatable CPython) |
| Postgres + pgvector | EDB binaries + community `vector.dll` | built from source (no portable official build exists; pgvector needs that build's `pg_config`), `rpath=$ORIGIN` so the folder is relocatable |
| Launcher | `Start-AIWorkspace.ps1` | `desktop/engine/launcher.py` (stdlib only, run by the engine's own Python) |
| Light code update | mirrored over the engine folder | written to `<data>/appcode/` — the AppImage is a **read-only** squashfs. The overlay remembers which bundled code it was made for and is dropped when a newer AppImage brings newer code |
| Data | `%APPDATA%\com.aiworkspace.app\data` | `~/.local/share/com.aiworkspace.app/data` |

The light update uses the **same** `app-latest` package as Windows (pure Python + static files
+ migrations), so one code release reaches both.

The launcher asks the kernel to SIGTERM it when the shell dies (`PR_SET_PDEATHSIG`), and the
API process likewise, so a crashed shell doesn't leave the port taken. A Postgres left running
by a `kill -9` is adopted (and stopped) on the next launch.

**Build the engine locally** (needs gcc, make, bison, flex, patchelf, zlib headers, Node 20+;
the work dir must be a real Linux filesystem — `initdb` refuses NTFS/exFAT):

```bash
bash desktop/engine/build-engine-linux.sh --work ~/aiw-engine   # -> ~/aiw-engine/aiworkspace-engine
~/aiw-engine/aiworkspace-engine/start-aiworkspace.sh            # runs it, opens the browser
```

The bundle needs glibc ≥ the build machine's: CI builds on `ubuntu-22.04` (glibc 2.35), so it
runs on Ubuntu 22.04+, Debian 12+, Fedora 36+ and current Arch. A build made on a newer distro
only runs on that glibc or newer.

**Packaging** (`cd desktop && npm run build`) produces the `.deb` anywhere. The AppImage step
(linuxdeploy) is meant for the CI image: on rolling distros such as Arch it fails inside
linuxdeploy itself — its bundled `strip` doesn't know the `.relr.dyn` section (`NO_STRIP=true`
gets past that) and its GTK plugin doesn't find the newer gdk-pixbuf layout. Build the AppImage
in CI or in an `ubuntu:22.04` container.

## Roadmap: embedded backend

Today the app needs the stack running. The path to a **self-contained** installer (no Docker)
has already been validated in a spike: a Postgres 16 binary + pgvector run embedded on Windows
and the 55 migrations pass. What's left is packaging the Python backend and supervising it from
the shell.

Technical details of the shell are in [`desktop/README.md`](../desktop/README.md).
