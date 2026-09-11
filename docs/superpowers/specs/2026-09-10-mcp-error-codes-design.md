# `mcp()` error codes — mapping, and what the agent service implements and tests

> Status: **implemented, 2026-09-10**, on branch `feat/mcp-tool-call` (commit range `feat/mcp-dashboard..feat/mcp-tool-call`, PR pending into `feat/mcp-dashboard`). Refines D9 ④ of `2026-09-08-mcp-dashboard-on-autoland-design.md` (the five-code set decided 09-10, §7 and §13 there). This document is the single place the full mapping lives; every other party sees only the subset in §2.
>
> Scope: the deepagent `POST /tool-call` endpoint, the classifier in `app/agent/connectors/mcp_adapter.py`, the two sentences the skill teaches the model, and the tests that pin all of it. Java and frontend responsibilities are listed only where they bound what deepagent does.
>
> Plan: `docs/superpowers/plans/2026-09-10-mcp-tool-call-endpoint.md` (2026-09-10; **row 4 and §10 item 2 are dropped by that plan's Task 3** — the endpoint never calls `tools/list`; an unknown tool is the server's `is_error` text under row 9. Also covers the landing-feedback wording for the handler argument shape, the skill description, and the 09-10 team decision that the `mcp()` runtime prelude (D9 hop ①) is injected by deepagent `results.py` at generation time, connector mode only — conditions and residual risk recorded there).

## 1. The five codes

Cut by "who can do something about it". Each code maps to exactly one remedy; anything finer goes into `message` for a human reading the card.

| `code` | Who acts | Remedy |
|---|---|---|
| `AUTH` | viewer | sign in again / reload |
| `RETRYABLE` | viewer | retry the same call |
| `TOOL_ERROR` | model or editor (argument values); sometimes the viewer by changing a filter | fix the call's argument values; the MCP server's own message says what is wrong |
| `INVALID_CALL` | model or editor | the dashboard's call itself is malformed or references something outside the session; fix the dashboard |
| `CONNECTOR_UNAVAILABLE` | editor or connector owner | the connector changed after the dashboard was written; nothing the page or the model can do |

Result shape on every hop, unchanged from D9: `{data: <raw structuredContent>}` or `{error: {code, message}}`, never both, never a third form.

## 2. What each audience needs to know

| Audience | Knows |
|---|---|
| Model (via `skills/mcp-data-dashboard/SKILL.md`) | the five names; `RETRYABLE` → card error + Retry button re-issuing the same `mcp()` call; `AUTH` → one page-level banner; anything else → card error. `message` is displayed verbatim, never rewritten |
| Repair prompt (D10) | `INVALID_CALL` and `TOOL_ERROR` are the model's to fix; calls that failed with the other three are left as they are |
| Frontend bridge | the five names; host-page timeout → `RETRYABLE`; Java non-200 folds as 401/403/404 → `AUTH`, 400/422 → `INVALID_CALL`, 5xx and network failure → `RETRYABLE`, status in `message`; forward only `TOOL_ERROR` and `INVALID_CALL` to `erd-artifact-error` |
| Java proxy | emits `AUTH` for the ownership 404 and `INVALID_CALL` for a connector not in the session's `selectedConnectors` (with the allowed ids in `message`), before calling deepagent; passes every deepagent result through unchanged; also folds deepagent's own 422 (malformed `tool`/`args`) to `INVALID_CALL` |
| deepagent | the full mapping in §4 |

The model never sees §4. Three of the five codes lead to identical page behaviour, and teaching the difference only invites three branches that do the same thing.

## 3. Endpoint

```
POST /tool-call
Authorization: Bearer <AGENT_API_BEARER_TOKEN>          same as /chat; failure → 401 (existing handler)
<SSO_TOKEN_HEADER>, <SSO_URL_HEADER>                     same names as /chat, from Settings
Body:  { connector: ConnectorSpec, tool: str, args: object }
       ConnectorSpec is the existing /chat shape: { id, name, url, bearerTokenKey? }
200:   { data: <structured_content unchanged> } | { error: { code, message } }
422:   request shape (empty `tool`, non-object `args`, missing `connector`); Java folds it to `INVALID_CALL`
```

Always 200 once past bearer auth. The endpoint does not run the model, does not create a workspace, does not open DuckDB, does not call `unwrap_envelope`, does not write `connector_calls.jsonl`. It calls the MCP server through the same `_call` path as chat mode, with the same `CONNECTOR_REQUEST_TIMEOUT_SECONDS` and `CONNECTOR_CALL_RETRIES`, and returns `structured_content` as-is. The endpoint does no listing of its own; the call goes through fastmcp's public `Client.call_tool`, which may list tools once per session for schema validation (accepted 09-11); see §5 and §4 row 4.

## 4. Classification (deepagent only)

First matching row wins. `message` templates name the actor's next move. No template ever includes SSO header values, bearer tokens, or argument values; argument keys are allowed.

| # | Condition (where detected) | `code` | `message` |
|---|---|---|---|
| 1 | Either SSO header missing or empty (`require_sso_token` / `require_sso_url` raise `LookupError`) | `AUTH` | `sign-in required: missing <header name>` |
| 2 | `connector.bearerTokenKey` set but `connector_bearer_token()` returns `None` (server deployment config) | `CONNECTOR_UNAVAILABLE` | `connector '<id>' is misconfigured on the server (bearer token key '<key>' not configured); ask the connector owner` |
| 3 | moved to the request schema (HTTP 422, folded to `INVALID_CALL` by hop ③) — 09-11: fastmcp does not reject either shape usefully on its own — an empty tool name reaches the server and comes back `is_error: Unknown tool: ''` (would classify `TOOL_ERROR`, the wrong code for a malformed call); non-object `args` makes fastmcp raise `pydantic.ValidationError` client-side, which the row-11 fallback would read as `RETRYABLE` (a pointless Retry button) | — | — |
| 4 | dropped 09-10 (see plan Task 3): the endpoint never calls `tools/list` at view time; an unknown tool arrives as the server's own `is_error` text and falls under row 9 (`TOOL_ERROR`) | — | — |
| 5 | `_call` raised with a transport cause after all retries: `asyncio.TimeoutError`, `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RemoteProtocolError`, or an `McpError` carrying a transport-level message | `RETRYABLE` | `connector '<id>' did not respond (<exception class>) after <n> attempts; retry` |
| 6 | `_call` raised for HTTP 401 or 403 from the MCP server (the viewer's SSO was rejected downstream) | `AUTH` | `connector '<id>' rejected your credentials (HTTP <status>); sign in again` |
| 7 | `_call` raised for any other HTTP 4xx from the MCP server (404 base URL, 410, …) | `CONNECTOR_UNAVAILABLE` | `connector '<id>' returned HTTP <status> at its base URL; ask the connector owner` |
| 8 | `_call` raised for HTTP 5xx from the MCP server | `RETRYABLE` | `connector '<id>' returned HTTP <status>; retry` |
| 9 | `CallToolResult.is_error` is true | `TOOL_ERROR` | the server's text content verbatim; fallback `tool '<tool>' failed with no message` |
| 10 | `structured_content is None` | `CONNECTOR_UNAVAILABLE` | `tool '<tool>' on connector '<id>' no longer returns structured data; ask the connector owner` |
| 11 | Any other exception | `RETRYABLE` | `unexpected failure calling '<id>.<tool>' (<exception class>); retry` — logged at ERROR with traceback |

Row 4, as originally specified, needed one `tools/list` call per unknown-tool guess. Dropped 09-10 (plan Task 3): the endpoint never lists tools at view time; `check_dashboard` already refuses an unknown connector/tool at write time, so a viewer who still hits one is in a `CONNECTOR_UNAVAILABLE`-shaped situation regardless, and the server's own `is_error` text (row 9) is precise enough for an editor to act on.

## 5. Changes in `mcp_adapter.py`

Today `_call` collapses every failure into `ConnectorToolError(str)` via `_actionable_message`, and the chat-mode wrapper only needs the string. The endpoint needs the category without parsing text.

- `ConnectorToolError` gains a `kind` attribute: `"transport"`, `"http"` with `status: int`, `"tool"`, `"no_structured_content"`, `"config"`. Existing constructor calls default to `kind="transport"` so nothing in chat mode changes.
- A `_classify_cause(exception) -> kind` helper walks `__cause__` / `__context__` from the exception `_run_with_retry` re-raises, and `_call` passes the result into the `ConnectorToolError` it builds. `_extract_tool_payload` sets `kind="tool"` and `kind="no_structured_content"` on the two errors it already raises.
- The endpoint switches on `kind` (and `status` for `http`) to produce rows 5–10. Row 11 is the `except Exception` fallback around the whole call.
- 09-11: `mcp_adapter.call_tool` goes through the public `client.call_tool(name, args, raise_on_error=False)` — it already wraps the request in the same session-monitoring fastmcp's own `tools.py` mixin uses, so a 4xx/5xx on the `tools/call` POST itself still surfaces immediately rather than waiting out the read timeout. The SDK's output-schema cache means the client may issue one `tools/list` per session (only on a session's first call to a given tool, and only when that call succeeds) — accepted, not worked around. `_extract_tool_payload` reads `is_error` / `structured_content` / `content` off fastmcp's own `CallToolResult` (`fastmcp.client.client.CallToolResult`), not the raw `mcp.types.CallToolResult`.

Nothing changes for the chat-mode wrapper, `check_dashboard`, landing, or prompts (the wrapper does gain one optional `logger.info` line, plan Task 8, reusing `classify_connector_error` for the failure `code`).

## 6. What deepagent does not classify

- Ownership and "connector not in this session": Java, which alone has the session. Emitted before deepagent is called.
- Host-page timeout: the frontend's `RETRYABLE`.
- Call budget: `CONNECTOR_CALL_BUDGET` is a per-turn limit on the model; view-time calls are per viewer per page, and any rate limiting is Java's (D9 ③: none in v1, observe per-artifact call rate first).

## 7. Logging

One line per call, same format as the chat-mode wrapper so both can be grepped together:

```
tool_call connector=<id> tool=<tool> arg_keys=[k1,k2] ms=<n> ok=true|false code=<code>
```

Never argument values, never header values. Row 11 additionally logs the traceback at ERROR.

## 8. Tests

All in `tests/`, using the `echo_server` fixture pattern from `test_mcp_adapter.py` (which already has an `is_error` tool and a `structured_content is None` tool), plus three more fixture servers: one that sleeps past `CONNECTOR_REQUEST_TIMEOUT_SECONDS`, one whose transport returns 401, and a URL nothing is listening on.

| Test | Pins |
|---|---|
| `test_tool_call_missing_sso_header_returns_auth` | row 1, per header |
| `test_tool_call_unconfigured_bearer_key_returns_connector_unavailable` | row 2 |
| `test_tool_call_empty_tool_name_returns_422` / `..._non_object_args_returns_422` | row 3 (09-11: moved to the request schema) |
| `test_tool_call_unknown_tool_returns_invalid_call_listing_available_tools` | row 4, message lists names |
| `test_tool_call_unknown_tool_relists_once_before_invalid_call` | row 4 cache miss behaviour |
| `test_tool_call_timeout_returns_retryable_after_configured_retries` | row 5, attempt count in message |
| `test_tool_call_connection_refused_returns_retryable` | row 5 |
| `test_tool_call_http_401_returns_auth_without_retry` | row 6 and the retry skip |
| `test_tool_call_http_404_base_url_returns_connector_unavailable` | row 7 |
| `test_tool_call_http_503_returns_retryable` | row 8 |
| `test_tool_call_is_error_returns_tool_error_with_server_text_verbatim` | row 9 |
| `test_tool_call_no_structured_content_returns_connector_unavailable` | row 10 |
| `test_tool_call_unexpected_exception_returns_retryable_and_logs_traceback` | row 11 |
| `test_tool_call_success_returns_structured_content_unchanged` | `data` is byte-identical to `structured_content`, including a FastMCP `{result: [...]}` envelope |
| `test_tool_call_never_unwraps_or_lands` | no DuckDB, no workspace dir, no `connector_calls.jsonl` after a call |
| `test_tool_call_response_never_contains_secrets` | inject the SSO token and bearer token into an exception message; assert neither appears in `message` |
| `test_tool_call_log_line_has_arg_keys_not_values` | §7 |
| `test_connector_tool_error_kind_defaults_keep_chat_mode_unchanged` | §5, existing wrapper tests still green |
| `test_tool_call_bearer_missing_returns_401` | existing `UnauthorizedError` handler applies |

Contract fixture for the other two teams: `tests/fixtures/mcp_result_examples.json`, one example per code with a realistic `message`, exported from these tests. The frontend and Java can load it to check their folding and display logic against the same strings.

## 9. Skill changes (deepagent owns, ships with D9)

`skills/mcp-data-dashboard/SKILL.md`, "Reading the response":

- Add: `r.error.code` is one of `AUTH | RETRYABLE | TOOL_ERROR | INVALID_CALL | CONNECTOR_UNAVAILABLE`.
- Replace the error branch example with:

```js
if (r.error) {
  if (r.error.code === 'AUTH') showAuthBanner(r.error.message);
  else showCardError(el, r.error.message, r.error.code === 'RETRYABLE' ? retry : null);
  return;
}
```

- Add a canonical `showCardError(el, message, retryFn)` and `showAuthBanner(message)` snippet for the model to copy, so error cards are uniform and `check_dashboard` has a stable pattern to look for.
- Repair prompt (D10 connector variant): one sentence, "`INVALID_CALL` and `TOOL_ERROR` are yours to fix; leave calls that failed with other codes unchanged."

`check_dashboard` (Phase B or later): warn when a handler's `r.error` branch never references `r.error.message`.

## 10. Open items

- **Resolved (Task 2 Step 5, tested against a real transport).** Rows 6 and 7 (401/403 and other 4xx) are distinguishable: a forced HTTP 401/403/503 from the MCP server reaches `httpx.HTTPStatusError` in the adapter's cause chain, so `ConnectorToolError.status` carries the real code and the message includes it (rows 6, 7, 8 all confirmed). A forced HTTP 404, however, never surfaces as `httpx.HTTPStatusError`: the MCP streamable-http client treats a 404 specially as "session terminated" and raises `McpError` before any status code is attached to an exception. That lands as `kind="transport"`, `status=None`, `detail="McpError"` — row 5's `RETRYABLE`, with `did not respond (McpError)` and no status number in `message` (the fallback the original bullet anticipated, minus the status — there is no status to include). See `test_tool_call_http_404_is_swallowed_as_session_terminated_returns_retryable` for the traceback evidence.
- **Resolved by dropping row 4 (plan Task 3).** The endpoint does no listing of its own; the call goes through fastmcp's public `Client.call_tool`, which may list tools once per session for schema validation (accepted 09-11) but this is not a pre-call validation step, so an unknown tool still arrives as the server's own `is_error` text under row 9 (`TOOL_ERROR`). The pre-call validation this item was reaching for stays where D9 ③ put it: a per-artifact allow-list held by Java (U5), fed by Phase B's `connector_calls.jsonl`, when that lands.
- Rate limiting for view-time calls stays with Java per D9 ③.
- **Row-9 `message` length is uncapped by deepagent.** `tool_reported_error` copies the server's text blocks verbatim, joined, with no truncation — deepagent never shortens a `TOOL_ERROR` message. Any cap on how much of it reaches the viewer belongs downstream, at Java hop ③ or the frontend error card, and is that team's call to make. The one length limit that does exist today is unrelated: the `mcp()` runtime prelude slices its own forwarded `erd-artifact-error` text (the `TOOL_ERROR`/`INVALID_CALL` echo onto the error-relay channel, §9) to 500 characters before it reaches Java's relay — that is a page-side safeguard against a giant error flooding the artifact error channel, not a cap on the `/tool-call` response body itself.
