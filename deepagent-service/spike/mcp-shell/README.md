# mcp-shell spike (THROWAWAY)

Manual e2e probe: can the deepagent generate a `dashboard.html` that calls the host-provided
`mcp(connector, tool, args, handler)`, and does it render + stay interactive inside a sandboxed
iframe whose `mcp()` calls are brokered to a real (mock) MCP server? No Java backend involved.

Run everything from `deepagent-service/`, four terminals:

1. `uv run python spike/mcp-shell/mock_server.py` — FastMCP `sales` connector on :8765 (`list_regions`, `list_orders`, `defect_summary`).
2. `uv run python spike/mcp-shell/bridge.py` — shell host on :8766 (`GET /`, `GET /api/dashboard`, `POST /api/mcp/call`).
3. `spike/mcp-shell/run-deepagent.sh` — deepagent on :8000 using the main checkout's `one-local.properties` (OpenRouter).
4. `AGENT_API_BEARER_TOKEN=spike-token spike/mcp-shell/generate.sh` — POSTs `/chat` in connector mode, writes `out/dashboard.html`.

Then open http://127.0.0.1:8766 and click **Load /api/dashboard** (or pick any HTML file).

Contract assumptions (confirm before productising): handler receives one object, `{data}` or
`{error:{message}}`; `data` is the MCP `structuredContent` verbatim — FastMCP wraps list returns as
`{"result": [...]}` and the agent adapter does not unwrap, so neither does the bridge.

`out/` keeps three snapshots of the agent's mid-turn `dashboard.html`, captured from the per-turn
scratch dir (`/tmp/erd-spike-workspace/.turns/*/`) by a throwaway watcher; the rest of that run's
captures were dropped as near-duplicates. They record how the model handled the unwrap question
above: `012655` reads `r.data`, `012805` unwraps both call sites to `r.data.result`, `023654`
reverts to `r.data` — the wrapper is peeled by the bridge instead (`UNWRAP_RESULT=1`). Chat logs
are gitignored (`*.log`).
