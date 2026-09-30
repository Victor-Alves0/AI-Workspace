# Observability

Admin → **Observabilidade**. Every operation becomes a **trace** (stored in Postgres,
`obs_traces`) made of nested **spans** (`obs_spans`) — what ran, how long it took, how much of
that was its own work, the database it touched and the numbers that explain it. Content (prompts,
messages) is never stored; only sizes, counts, ids and normalized SQL.

Code: `apps/server/aiworkspace/tracing/` (collection), `observability_routes.py` (analysis),
`apps/web/components/ObservabilityView.tsx` (screen).

## What is measured

| Where | Span | Numbers |
|---|---|---|
| Every HTTP request | trace `METHOD /route/{template}` | status, DB time/queries (queries outside spans are counted on the trace) |
| Turn setup (`@traced`) | `setup:tools`, `setup:skills`, `setup:agents`, `setup:autocompact`, … | duration + DB per step |
| Turn body | `turn:context`, `turn:tools_prompt`, `turn:user_message`, `turn:persist` | tools announced, prompt size, attachments |
| Model call | `llm:{model}` | `ttfb_ms` (first byte), `first_reasoning_ms`, `first_token_ms`, `reasoning_ms`, `generation_ms`, `tokens_per_s`, prompt/completion/cached/reasoning tokens, cost, prompt size, retries |
| Tool | `tool:{name}` (SIFT `execute_tool` shows the real path) | args/result size, error, `timed_out` |
| Agent | `agent:{name}` | depth, `queue_ms` (waiting for a pool slot), steps, output size |
| External HTTP (any `httpx` client) | `http:{host}` | method, path (query stripped, ids → `:id`), status, `until=headers` for streams |
| Web search | `search:query` → `search:{provider}` | cache hit/miss/shared, attempts, `pool_queue_ms`, `engines_ms`, results |
| Event loop | trace `runtime:loop-stall` | lag ≥ 250 ms with the traces that were open (suspects) |

## The chain

A trace started while another is active records `parent_trace` / `parent_span`, so the screen
walks the chain: `POST /chats/{id}/messages` → `chat:generation` → `agentes-bg:{team}` →
`bg:subagent-wake-…` → the woken `chat:generation`. How the link is made:

- `generation.start` creates its trace inside the request → linked automatically.
- `bg.spawn(...)` inside a trace runs the task in its own linked trace (`bg:{name}` or `trace=`);
  `trace=False` for pure timers. Tasks with no measured work are not stored.
- `run_turn_guarded` opens its own trace when there is none (channels) or the request's is
  already closed (API streaming body).

## Analysis endpoints (admin)

- `GET /observability/operations` — spans grouped by name: count, p50/p95/p99/max, **total** and
  **self** time (duration minus children), DB and HTTP. Sorted by total time.
- `GET /observability/operations/detail?name=` — every numeric attribute with avg/p50/p95,
  grouping by destination (path/tool/engine), top errors and slowest executions.
- `GET /observability/route?path=&method=` — per-call time by step, time outside any step,
  histogram, traces it spawns and slowest calls. Operations without a route group by name.
- `GET /observability/runtime` — loop lag (1/5/15 min), stalls and suspects, open traces,
  tasks, active generations, DB pool, memory, writer queue.
- `GET /observability/traces/{id}` — waterfall plus `parent` and `children`.

## Adding instrumentation

```python
from aiworkspace import tracing

@tracing.traced("setup:thing")          # a whole async function
async def load_thing(...): ...

with tracing.span("stage:x", kind="internal", size=n) as sp:   # a block
    ...
    sp.mark("first_result_ms")          # milestone, ms since the span opened (first time only)
    sp.set(results=len(found))
```

Telemetry never breaks or delays the request: the writer is a bounded in-memory queue flushed
in batches; errors in tracing are swallowed. Retention and sampling: `OBS_*` settings in
[configuration.md](configuration.md).
