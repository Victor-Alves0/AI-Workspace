# `aiw` — AI Workspace in the terminal

`aiw` is a terminal client for the **desktop app's engine**. It talks to the same local API
the window uses, so chats are shared both ways: a chat started in the terminal shows up in
the sidebar, and `aiw -c` picks up a chat you were using in the window.

```text
aiw                      new chat, interactive
aiw "question"           new chat starting with this message
aiw -c                   continue the last chat you used
aiw -r [N|ID|text]       resume a chat (no value: pick from a list)
aiw -p "question"        answer and exit; stdin is attached as context
aiw -m MODEL ...         model for a new chat (default: your profile's default model)
aiw ui                   open the desktop app
aiw chats | models       listings
aiw install              put `aiw` in ~/.local/bin (AppImage)
```

Inside a chat: `/new`, `/chats`, `/resume N`, `/model [ID]`, `/exit` (or Ctrl+D). Ctrl+C
stops the answer being generated (the partial answer is kept, like the window's Stop
button). A line ending in `\` continues on the next one.

Pipes work the way you'd expect: `cat build.log | aiw -p "why did this fail?" > answer.md`.
In `-p` mode stdout gets **only** the answer; tool calls and notices go to stderr.

Resuming a chat that is still generating (say, a long answer you started in the window)
attaches to it live and streams the rest.

## Installing the command

| Package | How you get `aiw` |
|---|---|
| `.deb` | installed as `/usr/bin/aiw` |
| AppImage | `./AI-Workspace.AppImage aiw install` writes `~/.local/bin/aiw`, which runs the AppImage |
| Windows | `aiw.cmd` in the install folder's `engine\` (adding it to PATH is still manual) |

Any `aiworkspace-desktop aiw ...` (or `AI-Workspace.AppImage aiw ...`) call runs the CLI in
the current terminal instead of opening the window.

## How it connects

- **Finds the engine** through `port.txt` in the app's data folder
  (`~/.local/share/com.aiworkspace.app/data`, `%APPDATA%\com.aiworkspace.app\data`; override
  with `AIW_DATA_DIR`). If the engine isn't running, `aiw` starts it **without a window** and
  stops it when it exits — nothing keeps running in the background after `aiw`.
- **Signs in without a password**: the launcher writes a random `cli-token` (mode `0600`) to the
  data folder; `aiw` sends it to `POST /api/auth/local` and gets the normal session cookies for
  the installation's **owner** (the first admin, created by the first-run setup). The route only
  exists in the desktop engine (`AIW_LOCAL_TOKEN_FILE`; a Docker install answers 404) and only
  accepts loopback connections. Anyone who can read that file can already read the database in
  the same folder, so it doesn't widen access — but it does skip 2FA for the owner on this
  machine.
- **Code updates**: the CLI is part of the `aiworkspace` Python package, so it arrives with the
  light `app-latest` update like the rest of the code. Markdown rendering uses `rich` when the
  engine has it (the `[cli]` extra, in engines built from this version on); without it, answers
  are printed as plain text.

## Limitations (MVP)

- Round-table chats are not listed (`mode != "single"`).
- No attachments (`@file`) or `$skill` / `#doc` references yet.
- An engine started by `aiw` stops when `aiw` exits: if you open the window while `aiw` is the
  one that started the engine, closing `aiw` takes the window's engine down with it.
