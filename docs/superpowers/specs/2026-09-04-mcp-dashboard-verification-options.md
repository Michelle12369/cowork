# MCP dashboard verification options

Date: 2026-09-04 · Branch: `feat/mcp-datasource` · Status: levels 1+2 implemented as the `check_dashboard` agent tool; level 3 deferred.

## Problem

In connector mode the agent writes a `dashboard.html` whose only data path is `mcp(connectorName, toolName, toolArgs, handler)`, a function the hosting shell injects into the iframe at view time. Nothing on the agent side executes that HTML, so three classes of defect reach the viewer as a blank card or a blank page:

1. **JavaScript syntax errors** — the whole inline `<script>` is discarded by the browser; no card ever leaves `loading`.
2. **Contract violations** — `mcp()` called with a connector id or tool name the session does not have, arg keys that no landed call used, forbidden APIs (`fetch`, `window.parent`, storage), a non-whitelisted CDN, `echarts.init` without the `erd` theme, or a handler that never checks `r.error`.
3. **Runtime/logic bugs** — handler throws on the real response shape, join never completes, control re-render leaks, chart height collapses.

The existing Java-side `JsSyntaxValidator` (GraalJS parse-only) covers class 1 only for the OpenAI-compatible provider path and never sees deepagent output. The deepagent path had no check at all.

## Options ladder

### Level 1 — JS syntax check (`node --check`) — **implemented**

Extract every inline `<script>` block (no `src`), write each to a temp file, run `node --check`, map the reported line back to the HTML line.

- Cost: ~50 ms per block, no network.
- Catches: unbalanced braces, bad template literals, stray `await` outside async, reserved-word misuse — the errors that make the browser drop the entire script.
- Misses: everything that is syntactically valid.
- Constraint: needs `node` on PATH. Local dev has node 26; the service image is `python:3.11-slim` **without node** — the tool reports "syntax check unavailable" and still runs level 2. Adding `nodejs` to the Dockerfile (~40 MB) or using `esprima`/`pyjsparser` were considered; the Python parsers do not understand `??`/`?.`/optional catch binding and reject valid modern code, so node is the only credible parser. Decision: keep node optional, add it to the image when the mcp mode ships.

### Level 2 — static contract lint — **implemented**

Regex/scanner-based checks over the same script blocks, driven by what the session actually knows:

| Check | Source of truth |
|---|---|
| `mcp(` first/second args are string literals | skill rule 1 |
| connector id ∈ session connectors; tool ∈ that connector's tools | `ChatRequest.connectors` |
| arg object literal keys equal the keys of a landed call | `replay/landings.jsonl` |
| tool never landed this session | `replay/landings.jsonl` |
| forbidden tokens: `fetch(`, `XMLHttpRequest`, `WebSocket(`, `EventSource(`, `window.parent`, `window.top`, `postMessage(`, `localStorage`, `sessionStorage`, `document.cookie`, `typeof mcp`, any definition of `mcp`, `__ERD_RESULTS__` | skill "Runtime environment" |
| `<script src>` ∈ {`https://cdn.tailwindcss.com`, `https://cdn.jsdelivr.net/npm/echarts@…`} | skill CDN whitelist |
| `echarts.init(el, 'erd')` | skill theme rule |
| at least one `mcp(` call; at least one `.error` reference | skill data contract |

- Cost: milliseconds, pure Python, no node dependency.
- Catches: the "confidently wrong" mistakes a small model makes most — hallucinated tool names, arg keys from the tool schema instead of from the call it made, `fetch` to the MCP URL directly.
- Misses: arg *values* (deliberately — they are dynamic from controls), response-shape mismatches, anything inside the handler body.
- Known false positives: `.error` appearing only in a comment satisfies the handler check; a `mcp(` call inside a string literal is still scanned. Acceptable for a lint that only produces findings the model must fix.

### Level 3 — headless render with stubbed `mcp()` — **deferred**

Load the HTML in headless Chromium (Playwright) with a prelude that defines `window.mcp` to resolve from `api_snapshots/{alias}.json`, looked up through `landings.jsonl` (connector_id + tool_name + args → land_as). Then:

- collect `pageerror` and `console.error`;
- after settle, assert no `[data-slot=loading]` visible and no `[data-slot=error]` visible for calls that have a snapshot;
- assert `canvas` count ≥ `echarts.init(` count (charts actually mounted, heights non-zero);
- dispatch `change` on every `<select>` and re-assert (interactivity smoke);
- screenshot for the audit trail / eval set.

- Cost: 1–3 s per run, Playwright + Chromium in the image (~300 MB), a stub that has to pick a snapshot when the call's args differ from any landed call (return the nearest landing, or `{error}`).
- Catches: class 3 — the bugs levels 1+2 cannot see, and the ones a viewer actually notices.
- Why deferred: image size and the snapshot-matching policy are product decisions; the spike first needs to show whether the on-prem model gets past levels 1+2 at all. If it does and class-3 bugs dominate, this is the next lever. It also doubles as the eval harness for the three-arm experiments already planned.

### Alternatives considered and dropped

- **GraalJS via the Java backend** (reuse `JsSyntaxValidator`): would route deepagent output through the Java side only to parse it; wrong place — the agent needs the finding *during* its turn to self-repair.
- **Ask the model to review its own code**: no ground truth; the same model that wrote the hallucinated tool name will approve it. Deterministic checks first, model judgment last (matches the project's "gpt-oss quality strategy = deterministic structure" decision).
- **jsdom instead of Chromium** for level 3: no `<canvas>`/layout, so ECharts cannot mount; would only prove the script does not throw at load.

## Gate finding (fixed alongside)

`DashboardSkillGateMiddleware` hard-coded `.skills/builtin/dashboard` as the skill that must be read before writing `dashboard.html`. In connector mode the agent was therefore forced to read the **file-mode** skill (`window.__ERD_DATA__` contract) and never gated on `mcp-data-dashboard`. Now `build_agent` picks `.skills/builtin/mcp-data-dashboard` when the turn has connectors.

## How the agent uses it

`check_dashboard` is registered only in connector mode (with the connector tools). The `mcp-data-dashboard` skill's workflow tells the model to run it after every `write_file`/`edit_file` of `dashboard.html` and to fix every finding before answering. Report format:

```
OK: no findings
```
or
```
3 finding(s):
- [syntax] line 142: SyntaxError: Unexpected token '}'
- [contract] line 88: tool "list_order" not found on connector "sales" (has: list_regions, list_orders, defect_summary)
- [contract] line 91: mcp("sales","list_orders") arg keys {days, region} match no landed call; landed key sets: {days}, {regions, days}
```
