# mcp-shell spike (THROWAWAY)

Manual end-to-end probe: can the deepagent generate a `dashboard.html` that calls
`mcp(connector, tool, args, handler)`, and does it render and stay interactive inside a sandboxed
iframe? Only two hops are stood in for. `shell.html` replaces the frontend host bridge (hop ②,
`ArtifactPanel`) and `bridge.py` replaces the Java proxy (hop ③), calling the real
`POST /tool-call` (hop ④) directly rather than mirroring the adapter locally. `mcp()` itself is
deepagent's injected `erd-mcp-runtime` prelude from `app/engine/results.py`, and `shell.html`
loads a dashboard as-is and warns when that block is missing, which means an old snapshot. The
page-facing contract is the `mcp-data-dashboard` skill's.

## Config

`DEV_`-prefixed keys come from `one-local.properties`, the same file `app/config.py` reads (see
`scripts/dev_config.py`). They are file-only, with no env var layer, though CLI flags
(`--base-url`, `--sso-token`/`--sso-url`, `--connector`) override per invocation. Run everything
from `deepagent-service/` so the service's own default path applies, or point
`ONE_PROPERTIES_PATH` at the file. `AGENT_API_BEARER_TOKEN` is the one official key here and still
goes through `app.config.get_settings()`, so env beats file beats default.

`bridge.py` fails at import unless all three hold:

- `AGENT_API_BEARER_TOKEN` is set, and matches what `run-deepagent.sh` started with.
- `DEV_CONNECTORS` has at least one entry. The step 1 mock server is enough.
- `DEV_SSO_TOKEN` and `DEV_SSO_URL` are set, unless every connector is on a loopback host.

## Run

Four terminals, in order:

1. `uv run python spike/mcp-shell/mock_server.py` — FastMCP `sales` connector on :8765, tools below.
2. `spike/mcp-shell/run-deepagent.sh` — deepagent serving the real `POST /tool-call`. Port from
   `DEV_DEEPAGENT_URL` (default 8000) and workspace root from `AGENT_WORKSPACE_ROOT` (default
   `/tmp/erd-spike-workspace`), both resolved by `scripts/dev_config.py --shell-exports` with the
   same parser the service uses, so the steps cannot drift. Runs with `--reload --reload-dir app`,
   so edits under `app/` need no restart.
3. `uv run python spike/mcp-shell/bridge.py` — shell host on :8766 (`GET /`, `GET /api/dashboard`,
   `POST /api/mcp/call`). A call naming a connector outside `DEV_CONNECTORS` gets `INVALID_CALL`,
   in the wording the product's Java hop would use.
4. `uv run scripts/dev_chat.py --state-dir spike/mcp-shell/out/.dev-session --dashboard-out spike/mcp-shell/out/dashboard.html "Build a sales dashboard from the sales connector..."`

Then open http://127.0.0.1:8766 and click **Load /api/dashboard**, or pick any HTML file.

Step 4 drives `/chat` in connector mode. The first run opens a session using `DEV_CONNECTORS`,
unless you pass `--connector ID URL [NAME]` or `--no-connectors`. Later runs with the same
`--state-dir` are follow-up turns carrying the history and the previous dashboard; `--new` starts
over. `--verbose` shows where each config value came from, naming secrets by source only. Every
turn preflights `/health` and each connector URL before posting, prints ERROR and failed STEP
events live, and prints the raw SSE log path up front for anything that does not.

### Mock server tools

| Tool | Returns | Exercises |
|---|---|---|
| `list_regions`, `list_orders`, `defect_summary` | a plain list, which FastMCP wraps as `{result: [...]}` | the normal path |
| `inventory_levels` | `{status, errorCode, data: [...]}`, and rejects an unknown `warehouse` | envelope shape, `TOOL_ERROR` |
| `shipment_summary` | a double envelope, `{result: {data: [...], total, days}}` | nested shape |
| `slow_orders(days)` | `list_orders`'s shape, after `MOCK_SLOW_SECONDS` (default 35, past the timeout) | `RETRYABLE` |
| `orders_text_only` | a JSON string with `output_schema=None`, so no `structuredContent` | `CONNECTOR_UNAVAILABLE` |

The mock server also publishes `skills/` to the agent through `SkillsDirectoryProvider`, so no
separate skill wiring is needed.

### Why not `uv run fastapi dev`

Both start the same uvicorn process on `app.main:app` with the same reload scope. The script
exists only so the four steps share one config file: it derives the port from `DEV_DEEPAGENT_URL`
and creates the workspace root, which the plain command leaves to you (match `--port` by hand and
set `AGENT_WORKSPACE_ROOT`, or get the service default `/data/workspace`, usually absent on a dev
machine). It can go away once the service has a port setting and a dev-friendly workspace default.

## Bridge behaviour

`DASHBOARD_HTML=<path>` serves a file other than `out/dashboard.html`.

**Internal runtime** (`AGENT_RUNTIME=internal`, public CDNs blocked): `bridge.py` does what
`ArtifactService.getHtml()` does in the product, rewriting the Tailwind and ECharts CDN URLs in
`shell.html` and `/api/dashboard` to `/vendor/...` and serving `frontend/public/vendor/` there.
Same two regexes as `erd.artifact.rewrite.profiles.tw3-ec5`. Off for every other runtime, so the
model's HTML is served untouched. Not applied to the choose-file path, which loads client-side;
use `DASHBOARD_HTML` instead.

**Head injection** (all runtimes): standing in for `ArtifactAssembler`, `bridge.py` renders
`backend/src/main/resources/templates/artifact/head-inject.vm` immediately after `<head>` and
serves `frontend/public/fonts/`, supplying the error relay, the Inter `@font-face` and the `erd`
ECharts theme when the HTML mentions `echarts`. The `__ERD_DATA__` branch is never taken. Its
renderer understands only the two Velocity constructs that template uses and raises on anything
else, so edit the template and the bridge together. Relay batches reach the shell log as
`[erd-artifact-error]`, the channel the prelude also uses to forward `TOOL_ERROR` and
`INVALID_CALL`, and what `ArtifactPanel` would receive. Per-call `[mcp]` lines cover the rest.

## Manual repair loop

Copy the failing card's own message, or the `[erd-artifact-error]` and `[mcp]` lines from the
page's log area, and paste it back as the next turn's message in step 4. `dev_chat.py` carries the
previous `dashboard.html` and the conversation history along automatically.

## The run that produced `out/`

`out/` holds snapshots from the last acceptance run. They predate this task's transport changes
and are replaced by the next run, per point 9 below. Chat logs are gitignored.

That run added to `one-local.properties`, and prefixed step 2 with an env var:

```properties
DEV_DEEPAGENT_URL=http://127.0.0.1:8010
AGENT_MODEL=qwen/qwen3.6-35b-a3b
AGENT_PROVIDER_REQUIRE_PARAMETERS=false
```

```bash
LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=0 ./spike/mcp-shell/run-deepagent.sh
```

The two `AGENT_*` keys are workarounds for that model, so drop them if you switch. The timeout
stays an env prefix because it is a langchain-openai knob, not a key `app.config.Settings` or
`scripts/dev_config.py` knows about. The non-default port needs nothing else: all four steps read
`DEV_DEEPAGENT_URL` from the same file.

## Acceptance

D8's three original points (spec §10) plus this task's six transport-side checks:

1. The first `dashboard.html` handler reads the layer named in the feedback's `Raw response shape`
   (`r.data.result` for the mock server's list-returning tools), without flip-flopping between
   `r.data` and `r.data.result` across turns.
2. A second turn asking only to swap two charts' positions does not re-call the connector, and
   `check_dashboard` reports OK.
3. An argument value the mock server rejects (`inventory_levels` with an unknown `warehouse`; note
   that `list_orders` with an unknown region just returns an empty list) makes the affected card
   show the server's error message, not a blank card.
4. A card calling a misspelt tool shows a `TOOL_ERROR` card reading `Unknown tool: …`.
5. A card calling `slow_orders` shows `RETRYABLE` with a Retry button, at the 60 second host
   timeout or the adapter's own timeout inside `/tool-call`, whichever fires first.
6. A card calling `orders_text_only` shows `CONNECTOR_UNAVAILABLE`, with no Retry button.
7. Starting `run-deepagent.sh` with a different `AGENT_API_BEARER_TOKEN` than `bridge.py` produces
   one `AUTH` banner, not one per card.
8. Every `[mcp]` line in the shell log shows the error code and the argument **keys**, never
   argument values.
9. The three original points still hold on a fresh model run, and the `out/` snapshots are
   replaced with that run's output. This ticks off the autoland plan's A6 Step 2.
