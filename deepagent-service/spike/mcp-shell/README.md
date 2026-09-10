# mcp-shell spike (THROWAWAY)

Manual e2e probe: can the deepagent generate a `dashboard.html` that calls the host-provided
`mcp(connector, tool, args, handler)`, and does it render + stay interactive inside a sandboxed
iframe whose `mcp()` calls are brokered, through the real `POST /tool-call` endpoint, to a real
(mock) MCP server? No Java backend involved, but every other hop is real: `mcp()` itself is
deepagent's injected `erd-mcp-runtime` prelude (not defined by this spike), and `bridge.py`'s
`/api/mcp/call` forwards to deepagent's actual endpoint instead of mirroring the adapter locally.

Run everything from `deepagent-service/`, four terminals, in this order:

1. `uv run python spike/mcp-shell/mock_server.py` — FastMCP `sales` connector on :8765 (`list_regions`, `list_orders`, `defect_summary` return plain lists, which FastMCP wraps as `{result: [...]}`; `inventory_levels` returns a `{status, errorCode, data: [...]}` envelope and rejects an unknown `warehouse`; `shipment_summary` returns a double envelope `{result: {data: [...], total, days}}`; `slow_orders(days)` sleeps `MOCK_SLOW_SECONDS` seconds — default 35, past `/tool-call`'s timeout — before answering with the same shape as `list_orders`, for a `RETRYABLE` card; `orders_text_only()` has `output_schema=None` and returns a JSON string, so it has no `structuredContent`, for a `CONNECTOR_UNAVAILABLE` card).
2. `AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/run-deepagent.sh` — deepagent on :8000 using the main checkout's `one-local.properties` (OpenRouter), serving the real `POST /tool-call`.
3. `AGENT_API_BEARER_TOKEN=spike-token uv run python spike/mcp-shell/bridge.py` — shell host on :8766 (`GET /`, `GET /api/dashboard`, `POST /api/mcp/call`). **Required env:** `AGENT_API_BEARER_TOKEN` — must equal the value step 2 started with; the bridge fails loudly at import if it is unset. Optional: `DEEPAGENT_URL` (default `http://127.0.0.1:8000`), `MOCK_MCP_URL` (default `http://127.0.0.1:8765/mcp`, forwarded as the connector spec's `url`), `DEV_SSO_TOKEN` / `DEV_SSO_URL` (dummy defaults `spike` / `http://spike.invalid`, sent as the two SSO headers `/tool-call` expects).
4. `AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/generate.sh [message]` — drives `/chat` in connector mode through the stateful dev client `scripts/dev_chat.py` (state in `out/.dev-session/`, gitignored), writes `out/dashboard.html`. First run opens a session; each later run is a follow-up turn on the same session with history and the previous dashboard carried along. `NEW=1` starts over. It preflights uv, the deepagent `/health`, the mock server and the token, and on failure prints the ERROR/STEP events and the tail of the raw SSE log.

Then open http://127.0.0.1:8766 and click **Load /api/dashboard** (or pick any HTML file).

The page-facing contract this spike implements is the mcp-data-dashboard skill's. The transport
side (D9) is now real end to end except the two hops that live outside deepagent: `shell.html`
stands in for the frontend's host bridge (hop ②, `ArtifactPanel`) and there is no Java proxy
(hop ③) — `bridge.py` calls deepagent's `POST /tool-call` (hop ④) directly. Where mcp() comes
from: deepagent's `app/engine/results.py` (`build_mcp_runtime_script`/`inject_mcp_runtime`)
injects the `<script id="erd-mcp-runtime">` block into any connector-mode `dashboard.html` at
generation time, so a dashboard produced by `generate.sh` already carries it — `shell.html` only
loads the file as-is and warns if the block is missing (an old snapshot from before this existed).

`out/` holds the snapshots from the latest acceptance run (see Acceptance below); they predate
this task's transport changes and are replaced by the next Acceptance run (see point 9 below).
Chat logs are gitignored (`*.log`).

## What was actually run

The four steps above are the nominal setup; the run that produced `out/` needed more. Step 3 was:

```bash
DEEPAGENT_PORT=8010 \
AGENT_MODEL=qwen/qwen3.6-35b-a3b \
AGENT_PROVIDER_REQUIRE_PARAMETERS=false \
LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=0 \
AGENT_API_BEARER_TOKEN=spike-token ./spike/mcp-shell/run-deepagent.sh
```

`AGENT_PROVIDER_REQUIRE_PARAMETERS=false` and `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=0` are
workarounds for the model above; drop them if you switch models. On a non-default port, step 4
needs `DEEPAGENT_URL=http://127.0.0.1:8010` to match.

`run-deepagent.sh` runs uvicorn with `--reload --reload-dir app`, so edits under `app/` restart the agent without re-running step 3.

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

Other knobs: `run-deepagent.sh` hardcodes `ONE_PROPERTIES_PATH` to the main checkout — that file is
gitignored and absent from worktrees, so set the env var elsewhere. `bridge.py` takes
`DASHBOARD_HTML=<path>` (serve a file other than `out/dashboard.html`), plus `DEEPAGENT_URL`,
`MOCK_MCP_URL`, `DEV_SSO_TOKEN`/`DEV_SSO_URL` and the required `AGENT_API_BEARER_TOKEN` for
`/api/mcp/call` (see step 3 above). The mock server publishes `skills/` to the agent itself via
`SkillsDirectoryProvider`, so no separate skill wiring is needed.

## Manual repair loop

Open `http://127.0.0.1:8766` and click **Load /api/dashboard**. The page's log area (the
`[erd-artifact-error]` relay batches and each per-call `[mcp]` line, which shows the error `code`
on failure) and each card's own error message are the feedback source: copy that text and paste
it back into `AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/generate.sh "<pasted error>"` —
`dev_chat.py` carries the previous `dashboard.html` and the conversation history along
automatically. `NEW=1` starts a fresh session.

## Acceptance

D8's original three points (spec §10) plus this task's six transport-side checks:

1. The model's first `dashboard.html` handler reads the layer named in the feedback's
   `Raw response shape` (`r.data.result` for the mock server's list-returning tools) without
   flip-flopping between `r.data` and `r.data.result` across turns.
2. A second turn that only asks to "swap the position of two charts" does not re-call the
   connector, and `check_dashboard` reports OK.
3. Deliberately passing an argument value the mock server rejects makes the affected card show the
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
