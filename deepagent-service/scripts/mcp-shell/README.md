# mcp-shell

Run a connector-mode dashboard end to end without the Java backend or the frontend: a mock MCP
server, the real deepagent, a small host page that stands in for the product's `ArtifactPanel`,
and `dev_chat.py` to drive `/chat`.

```mermaid
sequenceDiagram
    participant D as dev_chat.py
    participant A as deepagent :8000
    participant M as mock_server.py :8765
    participant B as browser + shell.html
    participant H as bridge.py :8766

    rect rgb(240,240,240)
    note over D,M: generate (step 4)
    D->>A: POST /chat (connectors, SSO headers)
    A->>M: tool calls, results land in DuckDB for this turn
    A-->>D: SSE, DASHBOARD_HTML
    D->>D: write out/dashboard.html
    end

    rect rgb(240,240,240)
    note over B,M: view (browser)
    B->>H: GET /api/dashboard
    H-->>B: dashboard in a sandboxed iframe
    B->>H: mcp(connector, tool, args) via postMessage, POST /api/mcp/call
    H->>A: POST /tool-call (bearer, SSO headers)
    A->>M: the tool call
    M-->>A: raw result
    A-->>H: {result} or {error: {code}}
    H-->>B: same body; page JS renders it
    end
```

`shell.html` plays hop ② (the frontend bridge) and `bridge.py` plays hop ③ (the Java proxy).
Hop ④, `POST /tool-call`, is the real one. `mcp()` is deepagent's injected `erd-mcp-runtime`
prelude; the page contract is the `mcp-data-dashboard` skill.

## Setup

Put these in `deepagent-service/one-local.properties` (copy `one.properties`):

| Key | Value for this loop |
|---|---|
| `DEV_CONNECTORS` | `[{"id":"sales","url":"http://127.0.0.1:8765/mcp"}]` |
| `AGENT_API_BEARER_TOKEN` | any string; every process reads the same file, so they agree |
| `AGENT_WORKSPACE_ROOT` | a writable directory, e.g. `/tmp/deepagent-workspace` |
| `DEV_SSO_TOKEN`, `DEV_SSO_URL` | only if a connector is not on localhost |
| `DEV_DEEPAGENT_URL` | only if deepagent is not on `:8000` |

Every key resolves as CLI flag > env > `one-local.properties` > default (`scripts/dev_config.py`).
Run all commands from `deepagent-service/`, or set `ONE_PROPERTIES_PATH`. Ports 8765 and 8766
are `_PORT` constants in the two files; deepagent's port is `DEV_DEEPAGENT_URL`.

## Run

Four terminals, in this order:

```bash
uv run python scripts/mcp-shell/mock_server.py
uv run fastapi dev --port 8000 --reload-dir app
uv run python scripts/mcp-shell/bridge.py
uv run scripts/dev_chat.py --state-dir scripts/mcp-shell/out/.dev-session \
    --dashboard-out scripts/mcp-shell/out/dashboard.html \
    "Build a sales dashboard from the sales connector: monthly revenue, top products, inventory by warehouse"
```

Then open http://127.0.0.1:8766 and click **Load /api/dashboard**.

Follow-up turns: run the last command again with a new message. It carries the history and the
previous dashboard. `--new` starts over, `--verbose` prints where every config value came from.

To repair a broken page, copy the failing card's message or the `[mcp]` / `[erd-artifact-error]`
lines from the page's log area and send them as the next message.

## When something fails

| Symptom | Cause and fix |
|---|---|
| `bridge.py` exits at import | It refuses to start without `AGENT_API_BEARER_TOKEN`, without at least one entry in `DEV_CONNECTORS`, or with a remote connector and no `DEV_SSO_*`. The message names the key. |
| `dev_chat.py` stops at preflight | deepagent's `/health` or a connector URL did not answer. Check the port against `DEV_DEEPAGENT_URL` and that the mock server is up. |
| `⚠️ ... 來自 env, 不是 one-local.properties` | An env var is overriding a value the file also sets, usually a stale `export`. The file is meant to win during a dev run; unset the variable. |
| Every card shows `AUTH` | deepagent and `bridge.py` started with different bearer tokens. |
| Log says `[shell] no erd-mcp-runtime block` and cards never load | The dashboard predates the injected prelude. Regenerate it with step 4. |
| Model stream stalls | `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=0 uv run fastapi dev ...`. A langchain-openai knob, not a config key. |

`out/` is gitignored. `DASHBOARD_HTML=<path>` makes the bridge serve another file.

## Mock server tools

| Tool | Returns | Exercises |
|---|---|---|
| `list_regions`, `list_orders`, `defect_summary` | a plain list, wrapped by FastMCP as `{result: [...]}` | the normal path |
| `inventory_levels` | `{status, errorCode, data: [...]}`; rejects an unknown `warehouse` | envelope shape, `TOOL_ERROR` |
| `shipment_summary` | `{result: {data: [...], total, days}}` | nested shape |
| `slow_orders(days)` | `list_orders` shape after `MOCK_SLOW_SECONDS` (default 35) | `RETRYABLE` |
| `orders_text_only` | a JSON string, no `structuredContent` | `CONNECTOR_UNAVAILABLE` |

The mock also serves `skills/` to the agent, so no extra skill wiring is needed.

## Acceptance checklist

1. The first dashboard reads the layer named in the feedback's `Raw response shape`
   (`r.data.result` for list tools) and does not flip between `r.data` and `r.data.result` on
   later turns.
2. A turn that only swaps two charts does not re-call the connector; `check_dashboard` reports OK.
3. `inventory_levels` with an unknown `warehouse` shows the server's message on that card, not a
   blank card.
4. A misspelt tool shows a `TOOL_ERROR` card reading `Unknown tool: …`.
5. `slow_orders` shows `RETRYABLE` with a Retry button, at the 60 s host timeout or the adapter's,
   whichever fires first.
6. `orders_text_only` shows `CONNECTOR_UNAVAILABLE`, no Retry button.
7. Mismatched bearer tokens produce one `AUTH` banner, not one per card.
8. Every `[mcp]` line in the shell log shows the error code and the argument keys, never values.
