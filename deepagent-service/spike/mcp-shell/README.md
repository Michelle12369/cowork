# mcp-shell spike (THROWAWAY)

Manual e2e probe: can the deepagent generate a `dashboard.html` that calls the host-provided
`mcp(connector, tool, args, handler)`, and does it render + stay interactive inside a sandboxed
iframe whose `mcp()` calls are brokered, through the real `POST /tool-call` endpoint, to a real
(mock) MCP server? No Java backend involved, but every other hop is real: `mcp()` itself is
deepagent's injected `erd-mcp-runtime` prelude (not defined by this spike), and `bridge.py`'s
`/api/mcp/call` forwards to deepagent's actual endpoint instead of mirroring the adapter locally.

Settings for this spike (deepagent URL, dummy SSO values, the `DEV_CONNECTORS` catalog) come
from `one-local.properties` — the same file `app/config.py` reads, `DEV_`-prefixed keys (see
`scripts/dev_config.py`). These `DEV_` keys are file-only: edit `one-local.properties` to change
them, CLI flags (`--base-url`, `--sso-token`/`--sso-url`, `--connector`) override per-invocation,
but there is no env var layer for them. `AGENT_API_BEARER_TOKEN` is the one official key here and
is read via `app.config.get_settings()` (env > file > default, unchanged), so it also needs to be
set there (or exported) before step 2. Put at least one entry in `DEV_CONNECTORS` (e.g. the mock
server started in step 1) or `bridge.py` refuses to start.

Run everything from `deepagent-service/`, four terminals, in this order:

1. `uv run python spike/mcp-shell/mock_server.py` — FastMCP `sales` connector on :8765 (`list_regions`, `list_orders`, `defect_summary` return plain lists, which FastMCP wraps as `{result: [...]}`; `inventory_levels` returns a `{status, errorCode, data: [...]}` envelope and rejects an unknown `warehouse`; `shipment_summary` returns a double envelope `{result: {data: [...], total, days}}`; `slow_orders(days)` sleeps `MOCK_SLOW_SECONDS` seconds — default 35, past `/tool-call`'s timeout — before answering with the same shape as `list_orders`, for a `RETRYABLE` card; `orders_text_only()` has `output_schema=None` and returns a JSON string, so it has no `structuredContent`, for a `CONNECTOR_UNAVAILABLE` card).
2. `spike/mcp-shell/run-deepagent.sh` — deepagent using this checkout's `one-local.properties` (port from `DEV_DEEPAGENT_URL`, default 8000; workspace root from `AGENT_WORKSPACE_ROOT` if the file sets it, else `/tmp/erd-spike-workspace`), serving the real `POST /tool-call`.
3. `uv run python spike/mcp-shell/bridge.py` — shell host on :8766 (no `PYTHONPATH` needed: it puts the service root on `sys.path` itself) (`GET /`, `GET /api/dashboard`, `POST /api/mcp/call`). Fails loudly at import if `AGENT_API_BEARER_TOKEN` is unset or `DEV_CONNECTORS` is empty. A `mcp()` call naming a connector id outside `DEV_CONNECTORS` gets back an `INVALID_CALL` body, mirroring the wording the product's Java hop would give for a connector not in the session's catalog.
4. `uv run scripts/dev_chat.py --state-dir spike/mcp-shell/out/.dev-session --dashboard-out spike/mcp-shell/out/dashboard.html "Build a sales dashboard from the sales connector..."` — drives `/chat` in connector mode through the stateful dev client (state in `out/.dev-session/`, gitignored), writes `out/dashboard.html`. First run opens a session (connectors default to `DEV_CONNECTORS`, or pass `--connector ID URL [NAME]` / `--no-connectors`); each later run with the same `--state-dir` is a follow-up turn on the same session with history and the previous dashboard carried along. `--new` starts over. It preflights `/health` and every connector URL before posting; ERROR and failed STEP events print live as they stream in, and the raw SSE log's path is printed up front at the start of the turn (for anything that doesn't show up live).

Then open http://127.0.0.1:8766 and click **Load /api/dashboard** (or pick any HTML file).

The page-facing contract this spike implements is the mcp-data-dashboard skill's. The transport
side (D9) is now real end to end except the two hops that live outside deepagent: `shell.html`
stands in for the frontend's host bridge (hop ②, `ArtifactPanel`) and there is no Java proxy
(hop ③) — `bridge.py` calls deepagent's `POST /tool-call` (hop ④) directly. Where mcp() comes
from: deepagent's `app/engine/results.py` (`build_mcp_runtime_script`/`inject_mcp_runtime`)
injects the `<script id="erd-mcp-runtime">` block into any connector-mode `dashboard.html` at
generation time, so a dashboard produced by `scripts/dev_chat.py` already carries it —
`shell.html` only loads the file as-is and warns if the block is missing (an old snapshot from
before this existed).

`out/` holds the snapshots from the latest acceptance run (see Acceptance below); they predate
this task's transport changes and are replaced by the next Acceptance run (see point 9 below).
Chat logs are gitignored (`*.log`).

## What was actually run

The four steps above are the nominal setup; the run that produced `out/` needed more. Step 2's
`one-local.properties` had:

```properties
DEV_DEEPAGENT_URL=http://127.0.0.1:8010
AGENT_MODEL=qwen/qwen3.6-35b-a3b
AGENT_PROVIDER_REQUIRE_PARAMETERS=false
```

and step 2 itself was:

```bash
LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=0 ./spike/mcp-shell/run-deepagent.sh
```

`AGENT_MODEL` and `AGENT_PROVIDER_REQUIRE_PARAMETERS=false` are workarounds for the model above
(drop them if you switch models) and belong in the properties file like any other Settings key.
`LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S` stays as an env var prefix because it is a
langchain-openai knob, not a config key `app.config.Settings` or `scripts/dev_config.py` know
about. On a non-default port, steps 3 and 4 then follow automatically: once `DEV_DEEPAGENT_URL`
is set in `one-local.properties`, `run-deepagent.sh` derives its `--port` from it and
`dev_chat.py`/`bridge.py` read the same key from the same file — no extra flag or env var needed.

`run-deepagent.sh` runs uvicorn with `--reload --reload-dir app`, so edits under `app/` restart the agent without re-running step 2.

Internal network (public CDNs blocked): when `AGENT_RUNTIME=internal` (read the same way the
deepagent reads it -- `one-local.properties` via `ONE_PROPERTIES_PATH`, env var wins), `bridge.py`
does what `ArtifactService.getHtml()` does in the product: rewrites the Tailwind/ECharts CDN URLs
in `shell.html` and `/api/dashboard` to `/vendor/...` and serves `frontend/public/vendor/` at
`/vendor/`. Same two regexes as `backend/src/main/resources/application.properties`
(`erd.artifact.rewrite.profiles.tw3-ec5`). Off for any other runtime, so the model's HTML is
served untouched. Not applied to the "or choose file" path (client-side load); use
`DASHBOARD_HTML=<path>` + Load instead.

Head injection (all runtimes): with no Java backend in the loop, `bridge.py` also stands in for
`ArtifactAssembler` on `/api/dashboard` -- it renders the repo's
`backend/src/main/resources/templates/artifact/head-inject.vm` (error relay, Inter `@font-face`,
and the `'erd'` ECharts theme when the HTML mentions `echarts`; the `__ERD_DATA__` branch is never
taken) and inserts it right after `<head>`, and serves `frontend/public/fonts/` at `/fonts/`.
The renderer only understands the two Velocity constructs that template uses and raises on
anything else, so edit the template and the bridge together. Batches from this relay arrive in
the shell log as `[erd-artifact-error]` — the same channel the prelude uses to forward a
`TOOL_ERROR`/`INVALID_CALL` `mcp()` result (per-call `[mcp]` lines cover the rest, see Acceptance
point 8), and what the product's `ArtifactPanel` would receive.

Other knobs: `run-deepagent.sh` no longer hardcodes `ONE_PROPERTIES_PATH` — the service's own
default (`one-local.properties` relative to cwd) applies, so run it from `deepagent-service/` and
point `ONE_PROPERTIES_PATH` elsewhere only if you keep the file somewhere else. It has no
`DEEPAGENT_PORT` env knob any more either: port and workspace root both come from
`one-local.properties` via `uv run python scripts/dev_config.py --shell-exports` (prints exactly
`DEEPAGENT_PORT=<port derived from DEV_DEEPAGENT_URL>` and
`AGENT_WORKSPACE_ROOT=<file value, or /tmp/erd-spike-workspace if the file doesn't set one>`, and
nothing else — same parser `app.config` uses, so it never disagrees with what the service itself
would read). `bridge.py` still takes `DASHBOARD_HTML=<path>` (serve a file other than
`out/dashboard.html`) as a plain env var; everything else it needs (`DEV_DEEPAGENT_URL`,
`DEV_SSO_TOKEN`/`DEV_SSO_URL`, `DEV_CONNECTORS`) comes from `one-local.properties` only — no env
var layer, edit the file to change them — plus the required `AGENT_API_BEARER_TOKEN`, which does
still go through `app.config.get_settings()` (env > file > default); see the settings paragraph
above. The mock server publishes `skills/` to the agent itself via `SkillsDirectoryProvider`, so
no separate skill wiring is needed.

## Manual repair loop

Open `http://127.0.0.1:8766` and click **Load /api/dashboard**. The page's log area (the
`[erd-artifact-error]` relay batches and each per-call `[mcp]` line, which shows the error `code`
on failure) and each card's own error message are the feedback source: copy that text and paste
it back into `uv run scripts/dev_chat.py --state-dir spike/mcp-shell/out/.dev-session --dashboard-out spike/mcp-shell/out/dashboard.html "<pasted error>"` —
`dev_chat.py` carries the previous `dashboard.html` and the conversation history along
automatically. `--new` starts a fresh session.

## Acceptance

D8's original three points (spec §10) plus this task's six transport-side checks:

1. The model's first `dashboard.html` handler reads the layer named in the feedback's
   `Raw response shape` (`r.data.result` for the mock server's list-returning tools) without
   flip-flopping between `r.data` and `r.data.result` across turns.
2. A second turn that only asks to "swap the position of two charts" does not re-call the
   connector, and `check_dashboard` reports OK.
3. Deliberately passing an argument value the mock server rejects (`inventory_levels` with an unknown `warehouse`; note `list_orders` with an unknown region just returns an empty list) makes the affected card show the
   server's error message, not a blank card.
4. A card calling a misspelt tool shows a `TOOL_ERROR` card reading `Unknown tool: …`.
5. A card calling `slow_orders` shows `RETRYABLE` with a Retry button (60 s host timeout, or the
   adapter's own timeout inside `/tool-call` — whichever fires first).
6. A card calling `orders_text_only` shows `CONNECTOR_UNAVAILABLE`, with no Retry button.
7. Starting `run-deepagent.sh` with a different `AGENT_API_BEARER_TOKEN` than `bridge.py` produces
   one `AUTH` banner, not one per card.
8. The shell log shows the error `code` and argument **keys** for every `[mcp]` line, never
   argument values.
9. D8's three original acceptance points above still hold on a fresh model run, and the `out/`
   snapshots are replaced with that run's output (this ticks off the autoland plan's A6 Step 2).
