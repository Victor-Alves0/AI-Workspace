# Desktop app (Windows)

A native app that opens AI Workspace in its own window, with a **tray icon**, **"run in the
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
repository). In this version the app does **not** embed the server — keep the stack running
(`docker compose up -d`) and open the app.

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

## Roadmap: embedded backend

Today the app needs the stack running. The path to a **self-contained** installer (no Docker)
has already been validated in a spike: a Postgres 16 binary + pgvector run embedded on Windows
and the 55 migrations pass. What's left is packaging the Python backend and supervising it from
the shell.

Technical details of the shell are in [`desktop/README.md`](../desktop/README.md).
</content>
