# Features

Full catalog of Singularity AI, grouped by area. Most features are configurable **per model**
and/or **per user** in the interface.

## Chat and models

- **Multi-model chat** via OpenRouter (any OpenAI-compatible model) and **local models via
  Ollama** (configurable under Connections).
- **Resumable streaming**: generation runs decoupled from the request — F5, switching chats or
  closing the browser doesn't cancel it; the stream is resumable. The **stop** button saves the
  partial.
- **Custom models**: build your own "GPTs" with a system prompt, parameters, tools,
  capabilities, filters, voice, memory and sub-agents — all per model. Editable slug.
- **Round table**: several models talking to each other, with you steering/pausing.
  Round-robin, manual or moderator (LLM) policies; a persona per participant.
- **Sub-agents**: an orchestrator delegates to worker models (sequential or parallel), with
  depth/loop/call limits and per-model permission. An `@` mention routes the turn.
- **Reference chats**: attach other conversations as context for a turn.
- **Non-destructive compaction**: messages leave the context but stay visible (with a summary);
  deleting the active node "uncompacts".
- **Temporary chat** (not saved) and **sharing** via public link (with optional
  password/expiry).

## Tools (SIFT)

The AI discovers and executes tools on demand. Categories: **native**, **Codespace** and
**integration**.

- **Web search** (DuckDuckGo, SearXNG, Tavily or Brave) and **deep search** (multi-step, with
  synthesis).
- **Page reading** and **headless browser**: the AI controls a real Chromium (navigate, click,
  type, screenshot) in a tab that persists per conversation. With an anti-SSRF guard.
- **Charts** (rendered as images for the channels), **diagrams** (Mermaid/Excalidraw),
  **financial quotes**, **real-time date/time**.
- **Video/audio transcription**: pulls the spoken content of a link (YouTube + ~1800 sites) via
  captions or STT, with rotating anti-blocking cookies.
- **Image generation**: native (a model with an image modality) or via a **router** that
  delegates to an image model; also **video** (Higgsfield).
- **Python code**: the AI writes and executes code in an **isolated sandbox** (a subprocess with
  CPU/memory/time limits).
- **Messaging agency**: the AI acts on your WhatsApp/Telegram/Discord connections
  (list/read/send).

Tools live under **Workspace → Tools** and are attachable per model.

## Memory and knowledge

- **Memory (mem0)** with **global / per model / per chat** scopes and **shareable memory
  stores** across models. Reads union the scopes; writes go per scope; optional review before
  saving. Controllable under Workspace → Memory, per chat and per model.
- **Knowledge Base (RAG)**: upload documents → indexed in pgvector (FastEmbed, 384 dim) →
  attached per model/chat. **Automatic** mode (injects snippets + cites) or **tool** mode
  (`search_knowledge`). Images indexed by name/tags and shown in the chat.
- **Second brain**: brains of **interlinked** notes (`[[wikilinks]]`) with an Obsidian-style
  graph; the AI can read/write and **propose skills** (with approval).
- **Proactive learning (Curator)**: in the background, the AI reviews conversations and suggests
  skills and memories — always with your approval. Opt-in.
- **Skill and prompt library**: import a skill from a link (`SKILL.md` from GitHub) or create
  reusable prompts, always through an approval card.

## Automation and channels

- **Scheduled automations** and **monitors** (price, page, search, RSS) that trigger the AI and
  notify you. Run history. A `Monitor` tool is available in the chat.
- **Notifications**: in-app, **Web Push** in the browser (VAPID) and delivery through the channels.
- **Messaging channels** — talk to your models outside the app; each conversation becomes a chat
  in the sidebar:
  - **WhatsApp**: unofficial via **Evolution API** (QR Code) or Meta's **official Cloud API**.
  - **Telegram**: a long-polling bot.
  - **Discord**: a channel via the Gateway (WebSocket).
  - Per-connection filters, memory and **context window**; batching of fragmented messages
    (debounce); visual output (a chart becomes a PNG, markdown sanitized).

## Platform

- **OpenAI-compatible public API** (`/v1/chat/completions`, `/v1/models`) with streaming,
  synchronous and asynchronous modes. See [public-api.md](public-api.md).
- **API key management**: create several, name them, revoke/regenerate, expiry, permissions,
  **limits** (RPM/RPD/monthly/tokens/concurrency), **budget** with blocking, **model policy**,
  per-key **memory modes**, IP allowlist and webhooks.
- **Observability**: each request becomes a _trace_ with spans (database time, reads/writes, LLM
  calls, tools). Admin panel with waterfall, percentiles (p50/p95/p99) and per-route series.
- **Usage analytics**: tokens, cost and requests per model and per origin; a ledger that
  survives chat deletion.
- **Codespace**: clone a repository (git/SSH/local), browse the **code graph**
  (Obsidian-style), edit files, keep chats and a **per-project** memory store, and drag
  files/snippets into the chat.
- **Playground**: **benchmarks** (with judge + rubric), side-by-side **comparisons** and **tool
  debugging**.

## Voice and media

- **Voice (TTS/STT)** via an OpenAI-compatible endpoint, including local **Kokoro** (opt-in) and
  **voice cloning**. Per-model voice (with blending). Voice input on the channels (audio → text).
- **Artifacts** (Claude-style): code, documents, HTML, SVG, Mermaid, CSV in a **dedicated
  window**, with preview/code, **version history** and editing.

## Integrations

- **Google Workspace**: Gmail + Calendar via OAuth (2 tools, multi-account, per-operation
  activation).
- **Tuya / Smart Life**: smart home via HMAC (automatic device discovery, per-model gating).
- **GitHub**: read and write (with confirmation), via PAT or OAuth.
- **Subscriptions**: use ChatGPT/Codex by login (OAuth), where applicable.

## Interface and experience

- **PWA / mobile**: responsive layout, installable, with safe-areas and drill-down navigation.
- **Desktop app (Windows)**: native window, tray, "run in the background" and "start with
  Windows". See [desktop.md](desktop.md).
- **Command palette** (Ctrl/⌘+K): a single launcher for actions, settings, models and chats,
  with fuzzy search.
- **Customizable keyboard shortcuts**; first-run **onboarding**; a **Status** panel; opt-in
  **personal budget** (warn/pause).
- **Composer**: paste/drag images, attach documents (`#`), skills (`$`), agents (`@`).
- **User security**: 2FA (TOTP), audit logs, ask for confirmation before sensitive actions
  (opt-in).
</content>
