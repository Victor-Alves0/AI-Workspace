# Turn Pipeline — order of operations, guards & checkpoints

The single most important map for debugging harness behavior. It traces **one turn**
end to end: from the user's input, through context/tool injection, the agentic loop,
every guard, to the final persisted answer.

Why it matters: a symptom often surfaces at a layer that is not its cause. A guard sits
*after* the thing it inspects, so `y → guard → x` can make it look like `y` is broken
when the real fault is in `x` (or in the guard's ordering). Use the **Guards &
Checkpoints** table and the **Debug by layer** section to look in the right place first.

> This page is **sequential** (one turn, in order). For the **cross-cutting** view — how
> the wrapping/observing/background layers depend on each other and fail *between* layers,
> plus the DB-session/event-loop discipline and a maintenance checklist — see
> [harness-coupling.md](harness-coupling.md).

> File anchors use `file:function` (line numbers drift). The canonical web path is
> `apps/server/aiworkspace/chat/messages_routes.py::send_message`. Every other entry
> point converges on the same `_prepare_turn → run_turn_guarded → run_turn` core.

---

## The six stages

```
1. ENTRY            HTTP route / channel / automation / API / wake
2. SETUP            _prepare_turn — provider, model config, SIFT, skills, guards, opts
3. GUARDED WRAPPER  run_turn_guarded — OUTER loop; output guards re-run the whole turn
4. RUN_TURN         context → tools+system assembly → agentic loop → final synthesis
5. TOOL DISPATCH    per tool_call: native handlers OR SIFT; confirmation/SSRF/exec gates
6. POST-TURN        usage finalize → done → generation driver persists (shielded)
```

Stages 1–2 run **inside the request** (the DB session is request-scoped). Stage 3+ run
in a **background driver** (`chat/generation.py::start`) so F5 / disconnect kills the
*subscriber*, never the generation.

---

## Sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant U as User / Channel
    participant R as Route (messages_routes)
    participant S as _prepare_turn (turn_setup)
    participant G as run_turn_guarded
    participant T as run_turn (orchestrator)
    participant D as _ToolDispatcher / SIFT
    participant P as LLM provider
    participant GEN as generation driver

    U->>R: input (text + attachments)
    R->>R: budget.enforce_or_raise · _resolve_provider
    R->>R: maybe_autocompact (BEFORE persist/history)
    R->>R: persist user msg · build history (excl. compacted)
    R->>S: _prepare_turn → model_config, sift, skills
    R->>R: _resolve_guards · _resolve_subagents · media/brain/memory opts
    R->>GEN: generation.start(run_turn_guarded(...), _finish)
    Note over GEN: background task; request just subscribes

    loop guard attempts (≤ max_retries, hard cap)
        GEN->>G: run_turn_guarded
        G->>T: run_turn(**kw, extra_system=reinforce?)
        T->>T: _gather_context (mem0 + KB-auto + #refs + ledger)
        T->>T: _assemble_tools_and_prompt (SIFT catalog/schemas)
        T->>T: _system_message (cached prefix + per-turn tail + PRIORITY last)
        loop agentic loop (≤ max_iters fuse)
            T->>P: stream_chat(messages, tools)
            P-->>T: tokens / tool_calls
            alt tool_calls
                T->>D: dispatch(name, args)
                D->>D: native handler OR SIFT dispatch
                D-->>T: result (or ask_options confirm / error+hint)
                T->>T: _absorb → anti-spin signature
                Note over T: repeat×N or empty stop → clean-prompt synthesis, break
            else final text
                T->>T: break with answer
            end
        end
        T-->>G: done{content, usage, tool_events}
        G->>G: output guard check (refusal / regex / judge+ledger)
    end
    G-->>GEN: final done
    GEN->>R: _finish → persist assistant msg + usage_event (shielded)
    R-->>U: SSE stream (live) · answer survives disconnect
```

---

## Stage detail

### 1. Entry points (all converge)

| Surface | Entry | Runner |
|---|---|---|
| Web send | `messages_routes::send_message` | `run_turn_guarded` via `generation.start` |
| Web regenerate | `messages_routes::regenerate_message` | `run_turn_guarded` |
| Web continue | `messages_routes::continue_message` | `run_turn_guarded` |
| Ephemeral (no persist) | `messages_routes::ephemeral` | `run_turn_guarded` |
| Wake (bg job done) | `chat/resume.py::resume_chat_turn` | `run_turn_guarded` |
| Channels (WA/TG/Discord/Slack) | `integrations/*_service.py` | `run_turn_guarded` |
| Automation / Monitor | `automation/runner.py` | `run_turn` |
| Public API `/v1` | `api/runner.py` | `run_turn` (raw — see note) |
| Roundtable / Playground | `chat/roundtable_routes.py`, `playground/` | `run_turn` (raw — intentional) |

> **Guards coverage:** every path that produces a normal chat reply — send, regenerate,
> **continue**, **wake**, channels, ephemeral — goes through `run_turn_guarded`, so a
> model's output guards apply consistently. The remaining raw `run_turn` paths are
> deliberate: Playground/Roundtable are debug/multi-model surfaces, and the public API
> is a passthrough. Automation is a candidate follow-up (it produces a user-facing
> message but currently runs raw).

**Front gates (before any model call), in order:** ownership check → model set →
non-empty → `budget_service.enforce_or_raise` (personal $ budget, HTTP 402 when
"pause") → `_resolve_provider` → **`maybe_autocompact`** → build history (skips
`compacted` rows) → persist user message.

> `maybe_autocompact` runs **before** history is built and the new message persisted —
> it summarizes the OLD history when context ≥ threshold × window. Reads the real
> `context_tokens`, never the cumulative `prompt_tokens`.

### 2. Setup (`turn_setup.py`)

`_prepare_turn` → `(api_key, base_url, model_config, sift, skills)`. Then the route
resolves, independently: `_resolve_guards` (output guards + grounding judge),
`_resolve_subagents`, `_memory_opts`, `_resolve_knowledge`, `_brain_setup`,
`_media_opts`, `_ref_docs`/`_ref_chats`. All resolved **in the request** (self-contained
dicts) so the background driver never touches the request DB session.

### 3. Guarded wrapper (`orchestrator.py::run_turn_guarded`)

An **outer loop** around `run_turn`. No guards → pure passthrough. With guards: run the
**entire** `run_turn`, capture the final `done`, then test each guard against the final
text. On a hit: **reinforce** (append `inject_text` to `extra_system`, re-run whole
turn) or **fallback_model** (switch model, re-run). Emits `guard` + `guard_reset` (front
discards the rejected attempt). Budget: per-guard `max_retries` (1–3) + a global
`_GUARD_HARD_CAP`. Usage of all attempts is summed into one final `done`.

> This is the classic `y → guard → x`: the guard sees only the *final* text, after the
> full loop + synthesis already ran. A "wrong answer re-runs forever" symptom lives
> here; the *reason* the answer was wrong lives in stage 4/5.

### 4. `run_turn` internals — exact order

1. **contextvars set** for tools: `current_chat_id`, `user_tz`, `background`,
   `user_profile`, `current_codespace_project_id/worktree` (isolated per async task).
2. **reasoning default**: absent `reasoning` key ⇒ injected `{"enabled": False}` (hybrid
   models must not silently think).
3. **`_gather_context`** → mem0 memories + KB-auto snippets + `#`-refs + ref-chats.
4. **`_assemble_tools_and_prompt`** → the announced tools array + SIFT prompt section
   (`TOOL_ACTION_GUARD` always appended; catalog only in list mode; pins inject full
   schemas). Builds the `_ToolDispatcher`.
5. **Ledger** loaded (`ledger_service.render_block`) — task state, first in the context
   block.
6. **`_system_message`** assembles the system prompt (see *Message assembly* below).
7. history appended (only if `use_context`) → user message + attachments
   (`_append_user_message`: files, audio via router, images via vision/OCR).
8. **Agentic loop** `for _iter in range(max_iters + 1)`:
   - cap fuse: last iterations strip `tools=None` to force a final answer;
   - **anti-spin**: `_absorb` signs `(tool, args, result)`; on repeat ≥
     `agent_noprogress_repeats` → clean-prompt synthesis + break (see below);
   - stream → if `tool_calls` → dispatch each (parallel when >1 and no `delegate`) →
     `_absorb`; else the model produced final text → break;
   - leaked-tool-call salvage runs only while `tools` is not None.
9. **Final synthesis** (`_final_synthesis`, stage-4 safety) when the loop ends with no
   text: A) same model on a **clean prompt** → B) auxiliary model → C) deterministic
   digest. Never a "regenerate" dead-end. Streams tokens live.
10. **Post-turn** (stage 6).

### 5. Tool dispatch (`_ToolDispatcher::run` → `_sift_dispatch`)

Order for each `tool_call`:

- **native, handled inline**: `view_skill`, `generate_image`, `search_knowledge`,
  `brain`, `propose_skill`, `delegate` (+ `run_code` gated by `code_mode`).
- everything else → **`sift.dispatch`** on a threadpool (builtins are sync).
- **reroute**: `unknown meta-tool` + dotted name → retried as
  `execute_tool{path, params}` (weak models call the path as the function name).
- **scope-error enrichment**: `not allowed in this scope` / `unknown tool` → error gets
  `_SCOPE_ERROR_HINT` so weak models recover instead of giving up.

Gates that live **inside** specific SIFT tool paths (they return an `ask_options`
confirmation card or an error, short-circuiting execution):

- **`_cs_confirm_guard`** — Codespace deletes, `git push`, exec, preview serve, task
  merge/discard (per-user confirmation toggle; bypassed under `toolctx.background`).
- **`exec_enabled`** — code exec refused unless the project has it on.
- **anti-SSRF** — browser/deep-search reject non-public URLs (`_is_public_url`).
- **`_RISKY_EXEC_RE` / `_is_long_runner`** — install/build/test commands get
  confirmation and/or auto-background (`code.exec.jobs`).

### 6. Post-turn & persistence

Inside `run_turn`, after the loop: spawn background **memory write** (mem0, only if
answered), **Curator** review (every N turns, opt-in), `_finalize_usage` (per-source
char attribution), set real `context_tokens`, emit `done`.

The **`generation` driver** (`generation.py::start`) consumes the event stream in a
detached task, accumulates terminal state (surviving mid-stream errors), and on finish
calls `_finish` **under `asyncio.shield`** → persists the assistant message + usage event
even if the client disconnected or the server is shutting down. User "Stop" cancels the
task, saves the partial, and re-propagates.

---

## Message assembly & cache boundary (`_system_message`)

```
┌─ system message ─────────────────────────────────────────────┐
│ static_system   = chat/agent prompt + SIFT section + skills   │  ← CACHED prefix
│                   + brain block                               │    (cache_control)
├───────────────────────────────────────────────────────────────┤
│ tail (never cached, changes per turn/attempt):                │
│   time_note                                                   │
│   context_block = ledger + memory + knowledge + #refs         │
│   PRIORITY block = extra_system (guard reinforce / channel)   │  ← read LAST = most weight
└───────────────────────────────────────────────────────────────┘
[ history (if use_context) ] → [ user message + attachments ]
```

The stable prefix is marked `cache_control` for explicit-cache providers; the per-turn
tail (time, ledger, memory, guard reinforcement) stays **outside** the cache so the big
prefix is reused across turns and guard retries. `extra_system` is deliberately last —
highest recency/priority — and is rewritten by `run_turn_guarded` on each retry.

---

## Guards & checkpoints — where each one sits

| Checkpoint | Layer / location | Gates / triggers | On trigger |
|---|---|---|---|
| Personal budget | Route, pre-model · `budget_service.enforce_or_raise` | $ spend vs cap ("pause") | HTTP 402 |
| Auto-compaction | Route, pre-history · `compaction_service.maybe_autocompact` | context ≥ threshold × window | summarize old history |
| `TOOL_ACTION_GUARD` | System prompt (always) · `orchestrator` | model tempted to claim an action without a tool | pushes toward `search_tools` |
| Reasoning default | `run_turn` start | no `reasoning` key | inject `{enabled:false}` |
| **Anti-spin** | Agentic loop · `_absorb` + loop top | same `(tool,args,result)` ×N / empty stop | clean-prompt synthesis + break |
| Iteration cap (fuse) | Agentic loop · `range(max_iters+1)` | too many iterations | strip tools → force final |
| Leaked tool-call salvage | Agentic loop · `_salvage_leaked_tool_calls` | tool-call syntax leaked as text (tools on) | reconstruct real call |
| Final synthesis | End of loop · `_final_synthesis` | loop ended with no text | A same-model clean / B aux / C digest |
| Confirmation cards | Inside SIFT tool path · `_cs_confirm_guard` | Codespace write/exec/push/merge | `ask_options` (bypassed if background) |
| exec_enabled | Inside SIFT · `code.exec.*` | project exec off | error, no exec |
| Anti-SSRF | Inside SIFT · `_is_public_url` | private/loopback URL | reject |
| Risky/long command | Inside SIFT · `_RISKY_EXEC_RE` / `_is_long_runner` | install/build/test | confirm and/or background |
| Scope-error hint | Dispatcher · `_sift_dispatch` | wrong/guessed tool path | error + recovery hint |
| **Output guards** | Around whole turn · `run_turn_guarded` | refusal / regex / judge (+ledger) on FINAL text | reinforce or fallback_model, re-run |
| Persistence shield | Driver · `generation._finalize` | client disconnect / shutdown | shielded commit of partial |

---

## Debug by layer (symptom → look here first)

- **"Paid, got 'regenerate/use another model'."** → stage 4 final synthesis / anti-spin.
  Should no longer happen; if it does, the model is format-locked *and* synthesis failed
  — check `_final_synthesis` logs (`síntese final … via <model>`).
- **"Model spins / hits the cap."** → anti-spin signature in `_absorb`. Same result must
  repeat *exactly*; polling that changes output won't trip it (by design).
- **"Answer keeps getting re-generated / swapped model."** → `run_turn_guarded` output
  guard. The *guard* re-ran the turn; the wrong-answer *cause* is stage 4/5, not the
  guard. Read the `guard` events (attempt, detect, rejected_preview).
- **"Guard didn't fire on a continue / wake turn."** → fixed: both now use
  `run_turn_guarded`. If a guard still doesn't fire there, check `_resolve_guards`
  returned it (model has `filter:output_guard` capability + enabled guard).
- **"Model says a tool doesn't exist / won't act."** → SIFT catalog exposure (list-mode
  only) + `TOOL_ACTION_GUARD` + scope-error hint. Check `search_tools` results and pins.
- **"Tool silently did nothing."** → confirmation card (`ask_options`) awaiting the user,
  or `exec_enabled`/SSRF/background gate. Look for a pending card, not a crash.
- **"Reminder fired at the wrong hour."** → `toolctx.user_tz` (channels read the saved
  profile tz; DB timestamps are UTC).
- **"Context shows 'overflow' with a tiny chat."** → the meter must read
  `usage.context_tokens`, not the cumulative `prompt_tokens`.
- **"Answer vanished on F5 / error."** → `generation` driver: partial is captured
  incrementally and persisted under `shield`; check `_final_message_fields`.
- **"KB/ledger not reaching the model."** → stage 4 steps 3–6 (`_gather_context`,
  ledger load, `context_block` composition).

---

## Invariants (don't break these)

- Setup resolves everything **in the request**; the background driver never uses the
  request DB session.
- Output guards inspect only the **final** text — they are an outer retry, not part of
  the loop.
- The cached system prefix must never include per-turn data (time, ledger, memory,
  guard reinforcement) — those live in the tail.
- The context meter and auto-compaction read `context_tokens` (turn base), never summed
  `prompt_tokens`.
- Anti-spin and final synthesis attack the *cause* (clean prompt) — never re-invite the
  stuck behavior on the polluted tool-call history.
- The Ledger is the safety net for task state across compaction (survives outside the
  message history).
