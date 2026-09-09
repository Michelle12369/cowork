# mcp-shell spike (THROWAWAY)

Manual e2e probe: can the deepagent generate a `dashboard.html` that calls the host-provided
`mcp(connector, tool, args, handler)`, and does it render + stay interactive inside a sandboxed
iframe whose `mcp()` calls are brokered to a real (mock) MCP server? No Java backend involved.

Run everything from `deepagent-service/`, four terminals:

1. `uv run python spike/mcp-shell/mock_server.py` — FastMCP `sales` connector on :8765 (`list_regions`, `list_orders`, `defect_summary` return plain lists, which FastMCP wraps as `{result: [...]}`; `inventory_levels` returns a `{status, errorCode, data: [...]}` envelope and rejects an unknown `warehouse`; `shipment_summary` returns a double envelope `{result: {data: [...], total, days}}`).
2. `uv run python spike/mcp-shell/bridge.py` — shell host on :8766 (`GET /`, `GET /api/dashboard`, `POST /api/mcp/call`).
3. `spike/mcp-shell/run-deepagent.sh` — deepagent on :8000 using the main checkout's `one-local.properties` (OpenRouter).
4. `AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/generate.sh [message]` — drives `/chat` in connector mode through the stateful dev client `scripts/dev_chat.py` (state in `out/.dev-session/`, gitignored), writes `out/dashboard.html`. First run opens a session; each later run is a follow-up turn on the same session with history and the previous dashboard carried along. `NEW=1` starts over. It preflights uv, the deepagent `/health`, the mock server and the token, and on failure prints the ERROR/STEP events and the tail of the raw SSE log.

Then open http://127.0.0.1:8766 and click **Load /api/dashboard** (or pick any HTML file).

The page-facing contract this spike implements is the mcp-data-dashboard skill's; the
transport-side contract (frontend prelude, Java proxy, deepagent tool-call endpoint, error codes)
is drafted in docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md §7 (D9) and is
not implemented here.

`out/` holds the snapshots from the latest acceptance run (see Acceptance below). The three
`dashboard-snapshot-*.html` files currently there predate the `Raw response shape` feedback and
show the model flip-flopping between `r.data` and `r.data.result`; they are replaced by the next
Acceptance run. Chat logs are gitignored (`*.log`).

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
anything else, so edit the template and the bridge together. Error relay batches arrive in the
shell log as `[erd-artifact-error]` next to the prelude's `[iframe error]` lines, so the same
error may be listed twice -- the former is what the product's `ArtifactPanel` would see.

Other knobs: `run-deepagent.sh` hardcodes `ONE_PROPERTIES_PATH` to the main checkout — that file is
gitignored and absent from worktrees, so set the env var elsewhere. `bridge.py` takes
`DASHBOARD_HTML=<path>` (serve a file other than `out/dashboard.html`). The mock server publishes
`skills/` to the agent itself via `SkillsDirectoryProvider`, so no separate skill wiring is needed.

## Manual repair loop

Open `http://127.0.0.1:8766` and click **Load /api/dashboard**. The page's log area
(populated by `window.onerror`) and each card's own error message are the feedback source: copy
that text and paste it back into
`AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/generate.sh "<pasted error>"` — `dev_chat.py`
carries the previous `dashboard.html` and the conversation history along automatically. `NEW=1`
starts a fresh session.

## Acceptance

From spec §10:

1. The model's first `dashboard.html` handler reads the layer named in the feedback's
   `Raw response shape` (`r.data.result` for the mock server's list-returning tools) without
   flip-flopping between `r.data` and `r.data.result` across turns.
2. A second turn that only asks to "swap the position of two charts" does not re-call the
   connector, and `check_dashboard` reports OK.
3. Deliberately passing an argument value the mock server rejects makes the affected card show the
   server's error message, not a blank card.
