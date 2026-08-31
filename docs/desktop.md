# Desktop app (Windows)

A native app that opens Singularity AI in its own window, with a **tray icon**, **"run in the
background"** and **"start with Windows"**. Built with [Tauri](https://tauri.app/).

## What it adds

The window loads the **same web interface** you use in the browser (by default
`http://localhost:3000`), so behavior is identical — what the shell adds is what a browser
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

## Preferences are per-machine

The options live in `%APPDATA%\com.aiworkspace.app\desktop-settings.json` — **not** in the
user's profile in the database. Storing them on the server would make the phone show "start with
Windows", and two PCs on the same account would fight over the value. That's why the **App**
category only appears in Settings when the UI runs inside the installed app.

## Install

The installer is published on **[Releases](../../releases)** (built by CI, not versioned in the
repository). It **embeds the whole engine** — Postgres, the Python backend, the Node frontend and
the embeddings model — so there is no Docker to run: install, open, and it boots the stack itself.

## Updates (two layers)

Reinstalling ~1 GB for a small code change would be painful, so updates are split in two:

- **Engine** (heavy: Python, dependencies, Postgres, Node, model) — versioned by
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

## Roadmap: embedded backend

Today the app needs the stack running. The path to a **self-contained** installer (no Docker)
has already been validated in a spike: a Postgres 16 binary + pgvector run embedded on Windows
and the 55 migrations pass. What's left is packaging the Python backend and supervising it from
the shell.

Technical details of the shell are in [`desktop/README.md`](../desktop/README.md).
