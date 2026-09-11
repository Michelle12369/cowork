# deepagent `POST /tool-call` — MCP view-time endpoint with the five error codes — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship hop ④ of the D9 transport contract: a deepagent endpoint that a dashboard's view-time `mcp()` call reaches (through the frontend bridge and the Java proxy, both out of scope here), calls the MCP server through the same adapter path chat mode uses, and answers with exactly one of `{data: <raw structuredContent>}` or `{error: {code, message}}` where `code` is one of `AUTH | RETRYABLE | TOOL_ERROR | INVALID_CALL | CONNECTOR_UNAVAILABLE`. Every failure the adapter can produce is mapped to one of the five by the classification table in the spec (§4, rows 1–11), the mapping is pinned by tests, and the model learns the shape from both places it reads: the skill (five codes, `RETRYABLE` → Retry button, `AUTH` → page banner, everything else → card error with `message` verbatim) and the wrapper's landing feedback (the handler argument is `{data: <this raw response>}` or `{error: {code, message}}`, not the raw response itself). Hop ① (the `mcp()` runtime prelude in the iframe) is also deepagent's, by team decision on 09-10: `results.py` injects it at generation time beside the results block (Task 7), so the frontend keeps only the host bridge.

**Architecture:** No model, no workspace, no DuckDB, no `unwrap_envelope`, no `connector_calls.jsonl`. `mcp_adapter.py` grows one async entry point (`call_tool`) on top of the existing `_call`/`_run_with_retry`/`_extract_tool_payload`, and `ConnectorToolError` carries a `kind` (+ `status`, `attempts`, `cause_name`, `detail`) so the endpoint can classify without parsing message text. A new pure module `app/agent/connectors/error_codes.py` holds the five-code classifier and the message templates (the single place the spec §4 table lives in code); `app/agent/connectors/tool_call_flow.py` orders the checks (rows 1–3 before the call, 5–11 after) and makes exactly one `tools/call` — never `tools/list` (row 4 dropped, Task 3); `app/main.py` adds the route next to `/chat` and `/repair` with the same bearer dependency and SSO header names. Chat mode keeps working unchanged: `_make_tool_call` becomes a thin sync wrapper over `call_tool`, all new `ConnectorToolError` attributes have defaults, and the wrapper still only reads `str(error)`.

**Tech Stack:** Python 3.11, FastAPI 0.141 (`Annotated` params, return-type response models, existing `RequireBearerToken` dependency), pydantic 2, fastmcp 3.x client (`StreamableHttpTransport`), httpx 0.28, pytest + pytest-asyncio (`asyncio_mode = "auto"`), ruff. Test MCP servers are real in-process `FastMCP` apps on random ports (pattern from `tests/test_mcp_adapter.py`).

**Status (2026-09-10):** plan written from the spec; nothing implemented. Branch for the work: `feat/mcp-tool-call` cut from `feat/mcp-dashboard`, merged back by PR (same route PR #81 took). Order relative to Phase B (`2026-09-08-mcp-dashboard-on-autoland.md` B1–B5) is still U1 in the decision summary; the two plans overlap in `skills/mcp-data-dashboard/SKILL.md` (different sections) and `wrapper.py` (Task 6 edits `describe_raw_response_shape`, Phase B's B2 edits `_execute`; Task 8's optional log line is in `_execute`) — whichever PR lands second rebases those two files. Task 7 touches `chat_turn.py` at the `inject_results` call, which Phase B's B4 also edits (a different block of `prepare()`); same rule.

**Spec:** `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md` (the full mapping; this plan implements all of §3–§9 and resolves the first two items of §10). Context: `2026-09-08-mcp-dashboard-on-autoland-design.md` §7 (D9 transport contract, five codes decided 09-10, invariants 1–5) and `2026-09-09-mcp-dashboard-decision-summary.md` §3–§4 (what is decided vs open).

## Glossary

| Term | Meaning | Where |
|---|---|---|
| **hop ④** | The deepagent leg of a view-time `mcp()` call: Java `POST /api/artifacts/{id}/mcp-call` → deepagent `POST /tool-call` → MCP server. Hops ①–③ (iframe runtime, host bridge, Java proxy) are separate plans. | `app/main.py`, `app/agent/connectors/tool_call_flow.py` |
| **code** | One of the five values in `r.error.code`. Cut by "who can act": viewer (`AUTH`, `RETRYABLE`), model/editor (`TOOL_ERROR`, `INVALID_CALL`), connector owner (`CONNECTOR_UNAVAILABLE`). Never a sixth value; HTTP status, hop, exception class all go into `message`. | `app/agent/connectors/error_codes.py` |
| **kind** | The adapter-level category on `ConnectorToolError`: `transport`, `http` (+ `status`), `tool`, `no_structured_content`, `config`. The classifier maps kind → code. Chat mode ignores it. | `app/agent/connectors/model.py` |
| **row N** | A row of spec §4's classification table. Tests are named after the code they pin, the plan text refers to rows. | spec §4 |
| **row 4 (dropped)** | Spec §4 row 4 wanted a `tools/list` before the call to answer `INVALID_CALL` for an unknown tool. Decided 09-10: the endpoint never calls `tools/list` at view time (the public listing is not something the deployment relies on); an unknown tool reaches the page as the MCP server's own `is_error` text (`Unknown tool: '<name>'`) under `TOOL_ERROR`, and `check_dashboard` already refuses unknown tools at write time. See Task 3. | `tool_call_flow.py` (one `tools/call`, nothing else) |
| **contract fixture** | `tests/fixtures/mcp_result_examples.json`: one realistic example response per code plus one success, kept byte-equal to what the templates produce. Java and the frontend load it to check their folding/display against the same strings. | Task 4 |

## Global Constraints

- Branch `feat/mcp-tool-call` from `feat/mcp-dashboard`; NEVER rebase or force-push once pushed; one commit per task; merge back via PR with `uv run ruff check .` clean, `uv run pytest -q` green, opus full-branch review "Ready to merge" in the PR description.
- Only `deepagent-service/` (incl. `skills/`, `spike/`) and `docs/superpowers/`. Zero Java, zero frontend. Where Java or the frontend must match a string or shape, this plan writes it into the contract fixture (Task 4) and the spec, nothing else.
- Endpoint invariants (spec §3, D9 invariants): always HTTP 200 once past bearer auth (422 only for pydantic shape failures); `data` is `structured_content` untouched; `args` are forwarded untouched; SSO only in headers, never in body, log, or `message`; exactly one of `data` / `error` in the body.
- `message` templates never include header values, bearer tokens, or argument **values**; argument **keys** are allowed. Exception text from the transport layer is never copied into `message` (class name only); the MCP server's own `is_error` text is copied verbatim (row 9) because that is the howto contract for actionable tool errors.
- Chat mode (`wrapper.py`, `check_dashboard`, landing, prompts) behaves identically before and after: the existing 487 tests stay green without edits other than the additive assertions this plan names.
- Same timeout and retry as chat mode (`CONNECTOR_REQUEST_TIMEOUT_SECONDS`, `CONNECTOR_CALL_RETRIES`); no extra overall deadline in the endpoint, and exactly one MCP request per call (`tools/call`; never `tools/list`, see Task 3). Note for the frontend/Java plans: worst case per request is `(1 + retries) × timeout` (60 s at defaults), the same as the 60 s host timeout D9 suggests — the host's `RETRYABLE` on timeout is the intended behaviour, not a bug here.
- Names: no 1–2 character identifiers (`id` as a domain word is fine); loop counters `index`/`rowIndex`. Comments 1–2 lines, purpose + how, no spec numbers or commit hashes. Raise/log/model-facing text in English.
- Tests: `test_<subject>_<condition>_<expected>`; assert element-level behaviour (code, message substrings/regex, request counts), never whole-string snapshots; fixture servers are real `FastMCP` apps, monkeypatching `mcp_adapter.Client` only where a real server cannot produce the failure (row 11, secrets).
- `get_settings()` is an `lru_cache` singleton — any test that sets `CONNECTOR_*`/`TOOL_CALL_*` env vars relies on the global autouse cache reset in `conftest.py`; the tool-name cache gets its own autouse reset in the endpoint test module.

## File structure

| File | Task | Action | Responsibility |
|---|---|---|---|
| `app/agent/connectors/model.py` | 1 | modify | `ConnectorToolError(message, *, kind, status, attempts, cause_name, detail)` with defaults |
| `app/agent/connectors/mcp_adapter.py` | 1 | modify | `_classify_cause`, retry skip on 401/403, `kind` on every raise, new `call_tool`, `_make_tool_call` delegates |
| `app/agent/connectors/error_codes.py` | 2 | new | `ErrorCode` literal, `ToolCallError` dataclass, message templates, `classify_connector_error(error, connector_id, tool_name)` |
| `app/agent/connectors/tool_call_flow.py` | 2, 3 | new | `execute_tool_call(request, sso_token, sso_url)`, rows 1–3 pre-checks, one `tools/call`, log line |
| `app/engine/request_context.py` | 2 | modify | `sso_identity(sso_token, sso_url)` context manager (sets only the two SSO contextvars) |
| `app/api/schemas.py` | 2 | modify | `ToolCallRequest`, `ToolCallErrorBody`, `ToolCallSuccess`, `ToolCallFailure` |
| `app/main.py` | 2 | modify | `POST /tool-call` route: bearer dependency, SSO headers, entry log |
| `tests/mcp_fixture_servers.py` | 2 | new | `free_port`, `run_server_in_thread`, `ForcedStatusMiddleware`, `RequestCountingMiddleware` extracted from `test_mcp_adapter.py` |
| `tests/test_mcp_adapter.py` | 1, 2 | modify | additive `kind` assertions; import helpers from `mcp_fixture_servers` |
| `tests/test_mcp_adapter_retry.py` | 1 | modify | retry-skip and classification tests |
| `tests/test_tool_call_endpoint.py` | 2, 3 | new | the spec §8 table (rows 1–3, 5–11, success, never-lands, secrets, log line, bearer 401) plus the row-4 replacement tests (unknown tool → `TOOL_ERROR`, `tools/list` never called) |
| `tests/fixtures/mcp_result_examples.json`, `tests/test_mcp_result_examples_fixture.py` | 4 | new | contract fixture + sync test |
| `skills/mcp-data-dashboard/SKILL.md`, `tests/test_mcp_dashboard_skill_text.py` | 5 | modify | frontmatter description; five codes, error branch, `showCardError` / `showAuthBanner`, three page-contract one-liners |
| `app/agent/connectors/wrapper.py`, `tests/test_connector_wrapper.py` | 6 | modify | landing feedback describes the handler argument (`{data: …}` / `{error: {code, message}}`) instead of "the raw response as r.data" |
| `app/engine/results.py`, `app/agent/chat_turn.py`, `app/agent/repair_flow.py`, `spike/mcp-shell/shell.html` | 7 | modify | `mcp()` runtime prelude block `erd-mcp-runtime` built and injected in connector mode, stripped on iteration/repair; spike stops injecting its own |
| `tests/test_results.py`, `tests/test_mcp_runtime_prelude.py` (new, quickjs), `tests/test_chat_turn_connectors.py`, `tests/test_repair.py` | 7 | modify/new | structural + behavioural tests of the prelude and its wiring |
| `README.md`, `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md`, `2026-09-09-mcp-dashboard-decision-summary.md`, `2026-09-08-mcp-dashboard-on-autoland-design.md`, `CLAUDE.md` | 8 | modify | endpoint documented; spec status → implemented; §10 items 1–2 resolved; summary §3 D9 row; hop ① amendment recorded |
| `app/agent/connectors/wrapper.py` | 8 (optional) | modify | one `logger.info` line in the same `tool_call ...` format |
| `spike/mcp-shell/bridge.py`, `shell.html`, `mock_server.py`, `README.md` | 9 | modify | bridge forwards to `/tool-call` (no local fastmcp client), non-200 folding, `event.source` check, `code` in the log, keys-only log; mock server gains one tool per failure class; README run order and acceptance list |

## Decision (2026-09-10, after team discussion): the `mcp()` runtime prelude is injected by deepagent in `results.py`

The 09-08 note that the prelude is injected by the frontend at srcdoc time was preliminary. The team's decision is that hop ① lives where the file-mode preamble already lives: `app/engine/results.py`, injected at generation time into the stored HTML, connector mode only. This section records what was checked before accepting it, the conditions that make it safe, and the residual risk, so nobody re-opens it without new facts.

**What was checked — is there another preamble in Java or the frontend?** No `mcp()` preamble exists anywhere. Three injection points exist today and none of them defines `mcp()`:

| Where | When | What it injects | Stored in the artifact? | Stripped before the model sees the HTML again? |
|---|---|---|---|---|
| deepagent `results.py` (`inject_results`) | generation: `chat_turn.py` before `DASHBOARD_HTML`, `repair_flow.py` after a repair | `<script id="erd-results-data">` — `window.__ERD_RESULTS__` data plus the row Proxy **runtime script** | yes | yes: `strip_injected_blocks` on `previousDashboardHtml` (`chat_turn.py`) and on the repair input (`repair_flow.py`), then re-injected fresh |
| Java `ArtifactAssembler` (`head-inject.vm`) | serve: every `GET` of the artifact | error relay (`erd-artifact-error` batches), Inter `@font-face`, `erd` ECharts theme, `window.__ERD_DATA__` when referenced | no — `AgentConversationWriter` stores the clean base | n/a |
| frontend `ArtifactFrame` (`injectCspMeta`) | srcdoc: every open | one CSP `<meta>` right after `<head>` (`script-src <origin> 'unsafe-inline'`, `connect-src 'none'`) | no | n/a |

So the file-mode precedent already exists: the results Proxy is a generation-time runtime script stored in the artifact, stripped and re-injected on every iteration and repair. Putting the `mcp()` prelude beside it is the same pattern, not a new one. The CSP allows inline scripts, so an inline prelude runs; the CSP meta is inserted after `<head>` by the frontend on the served HTML, so it lands before every injected block. Java's block and ours are independent (theme, fonts, error relay vs `mcp()`), order between them does not matter.

**Conditions that make it safe (all in Task 7):**
1. The block has its own id (`erd-mcp-runtime`) listed in `_INJECTED_SCRIPT_IDS`, so the existing strip on iteration and repair removes it. This matters beyond cleanliness: `check_dashboard` forbids `window.mcp =`, `postMessage(` and `window.parent` in the page, which the prelude uses; the prelude must never be in the workspace file the model edits and `check_dashboard` lints. Injection happens at emit time, after `check_dashboard` has run, exactly like `inject_results`.
2. The prelude must not install a second error relay: Java's `head-inject.vm` already forwards `window.onerror` / `unhandledrejection` as `erd-artifact-error`. The prelude only defines `mcp()`, dispatches results, and (D10 hook) forwards `{error}` results with `TOOL_ERROR` / `INVALID_CALL` on the same `erd-artifact-error` channel with arg keys, never values.
3. Injected only in connector mode: `chat_turn` knows `connector_specs`; `repair_flow` does not (until D10 adds `RepairRequest.connectors`), so repair re-injects the prelude when the input carried it and never adds one otherwise.
4. The block carries `data-erd-runtime="<version>"` so that, if a prelude bug ever has to reach already-published untouched dashboards, Java's assembler can strip-and-replace by id at serve time without regenerating artifacts. Not built now; the marker keeps the door open.
5. Hop ② (host bridge in `ArtifactPanel`: `event.source` check, Java proxy call, `erd-mcp-result` post, in-flight cap and timeout) stays in the frontend. The postMessage names and fields are frozen in D9, so the two halves can be released independently as long as neither renames a field.

**Residual risk, accepted:** a dashboard that is published and never iterated or repaired again keeps the prelude version it was generated with. File mode already accepts this for the Proxy script; condition 4 is the escape hatch.

The frontend plan shrinks accordingly: no prelude work, only the bridge. Task 8 records the amendment in the main spec (§7 hop ① row, §13 decision record) and the decision summary.

## Task 1: `ConnectorToolError` carries `kind`; adapter classifies causes and skips retry on rejected credentials

**Files:**
- Modify: `deepagent-service/app/agent/connectors/model.py`
- Modify: `deepagent-service/app/agent/connectors/mcp_adapter.py`
- Test: `deepagent-service/tests/test_mcp_adapter_retry.py`, `deepagent-service/tests/test_mcp_adapter.py`

**Interfaces:**
- Consumes: existing `_call`, `_run_with_retry`, `_extract_tool_payload`, `_build_headers`, `connector_bearer_token`.
- Produces:

```python
# model.py
ConnectorToolErrorKind = Literal["transport", "http", "tool", "no_structured_content", "config"]


class ConnectorToolError(Exception):
    """Raised when a connector call fails. `kind` says which layer failed so callers can act
    without parsing the message; chat mode only ever reads str(error)."""

    def __init__(
        self,
        message: str,
        *,
        kind: ConnectorToolErrorKind = "transport",
        status: int | None = None,
        attempts: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status          # HTTP status when kind == "http"
        self.attempts = attempts      # how many attempts _call made before giving up
        self.detail = detail          # the one extra string the kind needs: server text for
                                       # "tool", bearer key for "config", cause class for "transport"
```

```python
# mcp_adapter.py — new public entry point (async; chat mode's _make_tool_call wraps it)
async def call_tool(connector_id: str, base_url: str, tool_name: str, args: dict, bearer_token: str | None) -> object
```

- `_classify_cause(raised: BaseException) -> tuple[ConnectorToolErrorKind, int | None, str]` walks `__cause__` / `__context__` and, for `BaseExceptionGroup`, `.exceptions` (fastmcp wraps connect failures in `RuntimeError("Client failed to connect: …") from exception`, and anyio task groups can surface groups). First match wins: `httpx.HTTPStatusError` → `("http", response.status_code, "HTTPStatusError")`; `TimeoutError` (which is `asyncio.TimeoutError` on 3.11), `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RemoteProtocolError`, any other `httpx.TransportError`, `McpError` → `("transport", None, <that class name>)`; nothing matched → `("transport", None, type(raised).__name__)`.
- `_run_with_retry` retries every failure the same way, 401/403 included: a rejected credential answers `AUTH` after `1 + max(0, CONNECTOR_CALL_RETRIES)` attempts like anything else, not after one.
- `_call` builds `ConnectorToolError(_actionable_message(...), kind=kind, status=status, detail=cause_name if kind == "transport" else None, attempts=_max_attempt_count())`.
- `_extract_tool_payload`: `is_error` → `kind="tool", detail=<server text or None>`; `structured_content is None` → `kind="no_structured_content"`. Messages unchanged.
- `load_mcp_connector` missing bearer key → `kind="config"`. Message unchanged.

- [x] **Step 1: write the failing tests**

`tests/test_mcp_adapter_retry.py` (uses the existing `_FakeClient`; add a helper that builds an `httpx.HTTPStatusError`):

```python
def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://example.invalid/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


def test_http_401_is_not_retried_and_classified_as_http(monkeypatch):
    _FakeClient.configure([_http_status_error(401), "success"])
    monkeypatch.setattr(mcp_adapter, "Client", _FakeClient)

    with pytest.raises(ConnectorToolError) as error_info:
        asyncio.run(mcp_adapter._call("fixture", "http://example.invalid/mcp", "tools/call", {}, _return_ok))

    assert _FakeClient.enter_count == 1
    assert error_info.value.kind == "http"
    assert error_info.value.status == 401
    assert error_info.value.attempts == 1


def test_http_403_is_not_retried(monkeypatch): ...   # same shape, 403


def test_http_503_is_retried_and_classified_as_http(monkeypatch):
    _FakeClient.configure([_http_status_error(503), _http_status_error(503)])
    ...
    assert _FakeClient.enter_count == 2
    assert error_info.value.kind == "http"
    assert error_info.value.status == 503
    assert error_info.value.attempts == 2


def test_wrapped_connect_error_is_classified_transport_with_cause_name(monkeypatch):
    wrapped = RuntimeError("Client failed to connect: boom")
    wrapped.__cause__ = httpx.ConnectError("boom")
    _FakeClient.configure([wrapped, wrapped])
    ...
    assert error_info.value.kind == "transport"
    assert error_info.value.cause_name == "ConnectError"


def test_exception_group_cause_is_classified_transport(monkeypatch):
    grouped = ExceptionGroup("task group", [httpx.ReadTimeout("slow")])
    ...
    assert error_info.value.kind == "transport"
    assert error_info.value.cause_name == "ReadTimeout"


def test_unknown_exception_is_classified_transport_with_its_own_class_name(monkeypatch):
    _FakeClient.configure([ValueError("odd"), ValueError("odd")])
    ...
    assert error_info.value.kind == "transport"
    assert error_info.value.cause_name == "ValueError"


def test_connector_tool_error_kind_defaults_keep_chat_mode_unchanged():
    error = ConnectorToolError("plain")
    assert (error.kind, error.status, error.attempts, error.cause_name, error.detail) == (
        "transport", None, None, None, None
    )
    assert str(error) == "plain"
```

`tests/test_mcp_adapter.py` — additive assertions on existing tests (do not rename them):
- `test_server_error_raises_connector_tool_error_with_verbatim_message`: `error_info.value.kind == "tool"` and `error_info.value.detail == _FAILING_TOOL_MESSAGE`.
- `test_tool_call_without_structured_content_raises_actionable_error`: `kind == "no_structured_content"`.
- `test_bearer_token_key_declared_but_missing_from_dict_raises_fail_loud`: `kind == "config"`.
- `test_http_status_error_message_includes_status_code_for_diagnosis`: `kind == "http"` and `status == 401` — this is the proof for spec §10 item 1 that the status is reachable through fastmcp's cause chain on a real transport.

New tests in `test_mcp_adapter.py` for the entry point (echo_server fixture):

```python
async def test_call_tool_unknown_tool_raises_tool_kind_with_server_text(echo_server) -> None:
    with _identity(), pytest.raises(ConnectorToolError) as error_info:
        await call_tool("fixture", echo_server["base_url"], "no_such_tool", {}, None)
    assert error_info.value.kind == "tool"
    assert error_info.value.detail is not None and "no_such_tool" in error_info.value.detail


async def test_call_tool_returns_structured_content_unchanged(echo_server) -> None:
    with _identity():
        payload = await call_tool("fixture", echo_server["base_url"], "echo_tool", {"message": "hi"}, None)
    assert payload == {"echo": "hi"}
```

- [x] **Step 2: run, confirm failures**

Run: `cd deepagent-service && uv run pytest tests/test_mcp_adapter_retry.py tests/test_mcp_adapter.py -q`
Expected: new tests FAIL (`TypeError` on kwargs / `AttributeError: kind` / `ImportError` for the entry points); existing ones PASS.

- [x] **Step 3: implement `model.py` and `mcp_adapter.py`**

1. `model.py` as in Interfaces. Keep the class docstring to two lines.
2. `mcp_adapter.py`:
   - `_TRANSPORT_CAUSE_TYPES: tuple[type[BaseException], ...] = (TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.TransportError, McpError)`; import `httpx` and `from mcp.shared.exceptions import McpError`.
   - `_iter_cause_chain(raised)` generator: yields `raised`, then recurses into `.exceptions` for `BaseExceptionGroup`, then `__cause__`, then `__context__` (guard against cycles with an `id()` set).
   - `_classify_cause` as specified.
   - `_run_with_retry`: inside `except`, compute `kind, status, _ = _classify_cause(raised_exception)`; if `kind == "http" and status in _REJECTED_CREDENTIAL_STATUSES` → warning log `"MCP call rejected credentials: connector=%s method=%s url=%s status=%d, not retrying"` and `raise`. Keep the existing final-attempt log otherwise.
   - `_call`: compute `max_attempt_count` the same way `_run_with_retry` does (extract `_max_attempt_count()` helper used by both), classify, and raise with all attributes.
   - `call_tool` = `_call(..., "tools/call", headers, lambda client: client.call_tool(tool_name, args, raise_on_error=False))` → `_extract_tool_payload(result, tool_name, connector_id)`. No `tools/list` helper: the endpoint never lists tools (Task 3).
   - `_make_tool_call` → `def call(args): return asyncio.run(call_tool(connector_id, base_url, tool_name, args, bearer_token))`.
   - `_extract_tool_payload`: pass `kind="tool", detail=error_text or None` and `kind="no_structured_content"`.
   - `load_mcp_connector`: `kind="config"` on the missing-bearer raise.

- [x] **Step 4: run the whole suite**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: all green, 487 + the new tests. The retry tests that rely on "any exception is retried once" still pass because none of them uses 401/403.

- [x] **Step 5: commit**

`feat(deepagent): ConnectorToolError carries kind/status/attempts; adapter classifies causes, skips retry on 401/403, exposes call_tool`

## Task 2: `POST /tool-call` — schemas, SSO context, classifier, flow, route (rows 1–3 and 5–11)

> 09-11 update: row 3 (empty `tool`, non-object `args`) moved from a hand-written pre-call check
> into the pydantic request schema below — it is now a 422, folded to `INVALID_CALL` by hop ③
> (Java proxy / spike bridge), not one of deepagent's five codes. See the interfaces and test
> names below, already updated to match; `error_codes.py` no longer has `empty_tool_name` /
> `args_not_object`.

**Files:**
- Modify: `deepagent-service/app/api/schemas.py`
- Modify: `deepagent-service/app/engine/request_context.py`
- Create: `deepagent-service/app/agent/connectors/error_codes.py`
- Create: `deepagent-service/app/agent/connectors/tool_call_flow.py`
- Modify: `deepagent-service/app/main.py`
- Create: `deepagent-service/tests/mcp_fixture_servers.py` (helpers extracted from `test_mcp_adapter.py`)
- Modify: `deepagent-service/tests/test_mcp_adapter.py` (import the helpers; no behaviour change)
- Create: `deepagent-service/tests/test_tool_call_endpoint.py`

**Interfaces:**

```python
# app/api/schemas.py
class ToolCallRequest(BaseModel):
    connector: ConnectorSpec          # same shape as ChatRequest.connectors[] elements
    tool: str = Field(min_length=1)   # empty string is a 422, folded to INVALID_CALL by hop ③
    args: dict[str, Any]              # non-object is a 422, folded to INVALID_CALL by hop ③


class ToolCallErrorBody(BaseModel):
    code: Literal["AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"]
    message: str


class ToolCallSuccess(BaseModel):
    data: Any                          # structured_content, untouched


class ToolCallFailure(BaseModel):
    error: ToolCallErrorBody
```

Two models instead of one with optional fields so "never both, never a third form" is enforced by construction, and the route's return annotation `ToolCallSuccess | ToolCallFailure` gives FastAPI the response schema.

```python
# app/agent/connectors/error_codes.py  (pure: no FastAPI, no settings, no IO)
ErrorCode = Literal["AUTH", "RETRYABLE", "TOOL_ERROR", "INVALID_CALL", "CONNECTOR_UNAVAILABLE"]


@dataclass(frozen=True)
class ToolCallError:
    code: ErrorCode
    message: str


# one function per message template so tests and the contract fixture call the same code
def missing_sso_header(header_name: str) -> ToolCallError                      # row 1
def bearer_key_unconfigured(connector_id: str, key: str) -> ToolCallError      # row 2
def empty_tool_name() -> ToolCallError                                          # row 3
def args_not_object(type_name: str) -> ToolCallError                            # row 3
                                                                                 # row 4: none — see Task 3
def no_response(connector_id: str, cause_name: str, attempts: int) -> ToolCallError        # row 5
def credentials_rejected(connector_id: str, status: int) -> ToolCallError       # row 6
def base_url_error(connector_id: str, status: int) -> ToolCallError             # row 7
def server_error(connector_id: str, status: int) -> ToolCallError               # row 8
def tool_reported_error(tool: str, detail: str | None) -> ToolCallError         # row 9
def no_structured_data(connector_id: str, tool: str) -> ToolCallError           # row 10
def unexpected_failure(connector_id: str, tool: str, type_name: str) -> ToolCallError  # row 11


def classify_connector_error(error: ConnectorToolError, connector_id: str, tool: str) -> ToolCallError:
    """kind → rows 5–10. Row 2 (config) is also handled here so the wrapper log line can reuse it."""
```

Message strings are exactly spec §4's templates (copy them character for character; the contract fixture in Task 4 depends on it):

| row | template |
|---|---|
| 1 | `sign-in required: missing <header name>` |
| 2 | `connector '<id>' is misconfigured on the server (bearer token key '<key>' not configured); ask the connector owner` |
| 3 | `tool name is empty` / `args must be a JSON object, got <type>` |
| 4 | dropped (Task 3): an unknown tool is the server's `is_error` text under row 9 |
| 5 | `connector '<id>' did not respond (<cause class>) after <n> attempts; retry` |
| 6 | `connector '<id>' rejected your credentials (HTTP <status>); sign in again` |
| 7 | `connector '<id>' returned HTTP <status> at its base URL; ask the connector owner` |
| 8 | `connector '<id>' returned HTTP <status>; retry` |
| 9 | server text verbatim; fallback `tool '<tool>' failed with no message` |
| 10 | `tool '<tool>' on connector '<id>' no longer returns structured data; ask the connector owner` |
| 11 | `unexpected failure calling '<id>.<tool>' (<exception class>); retry` |

`classify_connector_error` mapping: `config` → row 2 (needs the key: read it from the error message is not allowed, so `ConnectorToolError` for config also gets `detail=<key>`; adjust Task 1 accordingly — `detail` is "the one extra string a row needs"); `transport` → row 5 with `error.cause_name` and `error.attempts or 1`; `http` 401/403 → row 6; `http` other 4xx → row 7; `http` 5xx → row 8; `http` anything else (1xx/3xx, should not happen) → row 8; `tool` → row 9 with `error.detail`; `no_structured_content` → row 10.

```python
# app/agent/connectors/tool_call_flow.py
async def execute_tool_call(
    request: ToolCallRequest, *, sso_token: str | None, sso_url: str | None
) -> ToolCallSuccess | ToolCallFailure:
    """Rows 1–3 before touching the network, then exactly one tools/call, then rows 5–11.
    Always returns; never raises past the row-11 fallback. Logs one `tool_call ...` line per call."""
```

Order inside (spec: first matching row wins, numeric order; row 4 dropped per Task 3):
1. Row 1: for `(settings.SSO_TOKEN_HEADER, sso_token), (settings.SSO_URL_HEADER, sso_url)` the first empty one → `missing_sso_header(name)`.
2. Row 2: `connector.bearerTokenKey` set and `connector_bearer_token(key) is None` → row 2.
3. Row 3: `not request.tool.strip()` → `empty_tool_name()`; `not isinstance(request.args, dict)` → `args_not_object(type(request.args).__name__)`.
4. `with sso_identity(sso_token, sso_url): payload = await call_tool(...)` → `ToolCallSuccess(data=payload)`. This is the only MCP request the endpoint ever makes.
5. `except ConnectorToolError as error` → `classify_connector_error(error, connector.id, tool)` (rows 2, 5–10).
6. `except Exception as error` → `logger.exception("tool_call unexpected failure connector=%s tool=%s", ...)` + row 11.
7. Log line (spec §7) in a `finally`-style wrapper around 1–6: `tool_call connector=<id> tool=<tool> arg_keys=[k1,k2] ms=<n> ok=true|false code=<code or ->` — `arg_keys` from `sorted(request.args)` when it is a dict, `[]` otherwise; on success `code=-`.

```python
# app/engine/request_context.py
@contextlib.contextmanager
def sso_identity(sso_token: str | None, sso_url: str | None) -> Iterator[None]:
    """Sets only the two SSO contextvars so mcp_adapter._build_headers can read them outside a
    /chat turn (no user/session id is involved in a view-time call)."""
```

```python
# app/main.py
@app.post("/tool-call")
async def tool_call(
    request: Annotated[ToolCallRequest, Body()],
    _auth: RequireBearerToken,
    http_request: Request,
) -> ToolCallSuccess | ToolCallFailure:
    logger.info("tool_call request connector=%s tool=%s arg_key_count=%d", ...)
    settings = get_settings()
    return await execute_tool_call(
        request,
        sso_token=http_request.headers.get(settings.SSO_TOKEN_HEADER),
        sso_url=http_request.headers.get(settings.SSO_URL_HEADER),
    )
```

- [x] **Step 1: extract fixture-server helpers**

Create `tests/mcp_fixture_servers.py` with `free_port()`, `run_server_in_thread(app, port) -> uvicorn.Server`, `ForcedStatusMiddleware`, `HeaderCapturingMiddleware` (moved verbatim from `test_mcp_adapter.py`, underscore prefix dropped), plus a new `RequestCountingMiddleware` (counts HTTP requests by JSON-RPC `method`; used by Task 3 to count `tools/list`). `test_mcp_adapter.py` imports them; its fixtures stay where they are. Run `uv run pytest tests/test_mcp_adapter.py -q` — still green.

- [x] **Step 2: write the failing endpoint tests**

`tests/test_tool_call_endpoint.py`. Shared pieces:

```python
from httpx import ASGITransport, AsyncClient
from app import main as main_module
from tests.conftest import TEST_BEARER_TOKEN
from tests.mcp_fixture_servers import ForcedStatusMiddleware, free_port, run_server_in_thread

_SSO_HEADERS = {"X-SSO-Token": "sso-token-value-not-in-messages", "X-SSO-Url": "https://sso.test.example/auth"}


def _connector(base_url: str, bearer_token_key: str | None = None) -> dict:
    return {"id": "fixture", "name": "Fixture Server", "url": base_url, "bearerTokenKey": bearer_token_key}


async def _post_tool_call(body: dict, headers: dict | None = None) -> httpx.Response:
    all_headers = {"Authorization": f"Bearer {TEST_BEARER_TOKEN}", **_SSO_HEADERS, **(headers or {})}
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=all_headers) as client:
        return await client.post("/tool-call", json=body)
```

Fixture servers (module scope, each a `FastMCP` app on `run_server_in_thread`):
- `echo_server`: `echo_tool(message) -> {"echo": message}`, `list_tool() -> list[dict]` (FastMCP wraps as `{"result": [...]}` — the never-unwraps proof), `text_only_tool` (`output_schema=None`, returns str), `failing_tool` (raises `ValueError(_FAILING_TOOL_MESSAGE)`), `slow_tool()` (`time.sleep(2)`; used with `CONNECTOR_REQUEST_TIMEOUT_SECONDS=0.3`).
- `status_server(401)`, `status_server(404)`, `status_server(503)` via `ForcedStatusMiddleware` — one parametrised factory fixture.
- `unreachable_url = "http://127.0.0.1:1/mcp"` (nothing listens).

Tests (every name from spec §8, one assertion block each; `body = response.json()`; every test also asserts `response.status_code == 200` and `set(body) in ({"data"}, {"error"})`):

| test | arrange | assert |
|---|---|---|
| `test_tool_call_missing_sso_header_returns_auth` (parametrised over the two headers) | drop one SSO header | `code == "AUTH"`, message `== f"sign-in required: missing {header}"`, and the fixture server's captured requests stay empty (no network before auth) |
| `test_tool_call_unconfigured_bearer_key_returns_connector_unavailable` | `bearerTokenKey="missing-key"`, `CONNECTOR_BEARER_TOKENS` unset | `CONNECTOR_UNAVAILABLE`, message names `'fixture'` and `'missing-key'` |
| `test_tool_call_empty_tool_name_returns_422` (09-11: was `..._returns_invalid_call`) | `tool=""` | `status_code == 422` |
| `test_tool_call_non_object_args_returns_422` (09-11: was `..._returns_invalid_call`) | `args=[1, 2]` | `status_code == 422` |
| `test_tool_call_timeout_returns_retryable_after_configured_retries` | `slow_tool`, timeout 0.3 s, retries 1 | `RETRYABLE`, message matches `r"connector 'fixture' did not respond \(\w+\) after 2 attempts; retry"` |
| `test_tool_call_connection_refused_returns_retryable` | `unreachable_url` | `RETRYABLE`, message contains `did not respond (` and `; retry` |
| `test_tool_call_http_401_returns_auth_without_retry` | `status_server(401)`, retries 1, `RequestCountingMiddleware` | `AUTH`, message `== "connector 'fixture' rejected your credentials (HTTP 401); sign in again"`, HTTP request count equals the count observed with retries 0 (measure both in the test) |
| `test_tool_call_http_404_base_url_returns_connector_unavailable` | `status_server(404)` | `CONNECTOR_UNAVAILABLE`, message contains `HTTP 404 at its base URL` |
| `test_tool_call_http_503_returns_retryable` | `status_server(503)` | `RETRYABLE`, message `== "connector 'fixture' returned HTTP 503; retry"` |
| `test_tool_call_is_error_returns_tool_error_with_server_text_verbatim` | `failing_tool` | `TOOL_ERROR`, `message == _FAILING_TOOL_MESSAGE` (not the adapter's wrapped sentence) |
| `test_tool_call_no_structured_content_returns_connector_unavailable` | `text_only_tool` | `CONNECTOR_UNAVAILABLE`, message `== "tool 'text_only_tool' on connector 'fixture' no longer returns structured data; ask the connector owner"` |
| `test_tool_call_unexpected_exception_returns_retryable_and_logs_traceback` | monkeypatch `tool_call_flow.call_tool` to raise `KeyError("boom")`; `caplog` at ERROR | `RETRYABLE`, message `== "unexpected failure calling 'fixture.echo_tool' (KeyError); retry"`, caplog has a record with `exc_info` |
| `test_tool_call_success_returns_structured_content_unchanged` | `echo_tool` and `list_tool` | `body == {"data": {"echo": "hi"}}`; `body == {"data": {"result": [...]}}` (envelope kept) |
| `test_tool_call_never_unwraps_or_lands` | `AGENT_WORKSPACE_ROOT=tmp_path/ws`; monkeypatch `app.engine.api_snapshot.unwrap_envelope` and `land_response` to raise `AssertionError` | success; `not (tmp_path / "ws").exists()`; no `connector_calls.jsonl` anywhere under `tmp_path` |
| `test_tool_call_response_never_contains_secrets` | `CONNECTOR_BEARER_TOKENS={"key": "bearer-secret-xyz"}`, `bearerTokenKey="key"`; monkeypatch `mcp_adapter.Client` to raise `RuntimeError(f"leak {sso} {bearer}")` | message contains neither the SSO token nor `bearer-secret-xyz` (row 11 copies only the class name) |
| `test_tool_call_log_line_has_arg_keys_not_values` | `echo_tool` with `{"message": "value-must-not-be-logged"}`; `caplog` at INFO | one record matching `tool_call connector=fixture tool=echo_tool arg_keys=[message] ms=\d+ ok=true code=-`; `value-must-not-be-logged` not in `caplog.text` |
| `test_tool_call_bearer_missing_returns_401` | no `Authorization` | `status_code == 401`, `body == {"error": "unauthorized"}` |
| `test_tool_call_malformed_body_returns_422` | body without `connector` | 422 (the shape Java folds to `INVALID_CALL`) |

- [x] **Step 3: run, confirm failures**

Run: `cd deepagent-service && uv run pytest tests/test_tool_call_endpoint.py -q`
Expected: all FAIL with 404 from the missing route.

- [x] **Step 4: implement**

1. `request_context.sso_identity` (set both vars, `try/finally` reset).
2. `schemas.py` models; extend the module docstring to name the third endpoint.
3. `error_codes.py` exactly as in Interfaces. Row 2 needs the key: in Task 1's `load_mcp_connector` raise, also pass `detail=bearer_token_key` (one-line addition; keep Task 1's test green).
4. `tool_call_flow.py`: `execute_tool_call` with the ordering above.
5. `main.py` route; add `ToolCallRequest`, `ToolCallSuccess`, `ToolCallFailure` to the `__all__` re-export list only if a test needs them (it does not; skip).

- [x] **Step 5: verify spec §10 item 1 against the real transport**

While the 401/404/503 tests run against `ForcedStatusMiddleware`, confirm the status reaches `ConnectorToolError.status` through fastmcp's cause chain (the Task 1 additive assertion on the 401 test already proves it for `tools/list`; the endpoint tests prove it for `tools/call`). If 404 turns out to be swallowed by the mcp client into a generic error (it has special handling for expired sessions), do **not** loosen the test: record what the transport actually surfaces in spec §10 and change the expected message to spec's stated fallback (`RETRYABLE` with the status in `message`) only with that note written first.

- [x] **Step 6: run everything**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: green. Check `tests/test_api_auth.py` still passes — the new route uses the same dependency.

- [x] **Step 7: commit**

`feat(deepagent): POST /tool-call — view-time MCP call with the five error codes (rows 1–3, 5–11), no unwrap, no landing`

## Task 3: Row 4 re-evaluated — no `tools/list` at view time; unknown tool is the server's answer

**Decision (09-10):** the public `tools/list` will not be used by the endpoint. Spec §4 row 4 (list tools, answer `INVALID_CALL` with the available names, cache per url) and §10 item 2 are dropped.

**Why it is not needed:**
- **What actually happens without it, verified in the SDK:** the MCP low-level server turns any exception in `call_tool` into `CallToolResult(isError=True, content=[text])`, and fastmcp raises `NotFoundError("Unknown tool: '<name>'")` for a name it does not have. So an unknown tool arrives as an `is_error` result, the adapter classifies it as kind `tool`, and the endpoint answers `TOOL_ERROR` with `Unknown tool: 'list_order'` verbatim (row 9). The card shows it; an editor can act on it. No hop needs to parse it.
- **The write-time gate already covers the model's mistake:** `check_dashboard` (Phase A, D1–D4 (i)) refuses a `dashboard.html` that names a connector or tool not in the session, so a dashboard that reaches a viewer with an unknown tool means the connector changed after publishing — a `CONNECTOR_UNAVAILABLE`-shaped situation that neither the page nor the viewer can fix either way. The extra precision of row 4 bought nothing the page acts on differently.
- **Cost removed:** one network round trip per call (or a cache with TTL, a per-url leak of tool names across viewers, and a config knob), plus a second failure surface (`tools/list` failing on a server whose `tools/call` works).
- **The future pre-call validation stays where D9 ③ put it:** a per-artifact allow-list held by Java (U5, fed by Phase B's `connector_calls.jsonl`) can answer `INVALID_CALL` before deepagent is called, with no listing at all.
- **Rejected alternative:** sniffing the `is_error` text for `Unknown tool` to upgrade it to `INVALID_CALL`. The wording is fastmcp's, not the protocol's; a heuristic that fires for one server implementation and not another is worse than a stable `TOOL_ERROR`.

**Found during implementation (09-10):** the MCP SDK's `ClientSession.call_tool` issues its own `tools/list` after every successful call whose tool is not yet in the session's output-schema cache, to validate `structuredContent` (`mcp/client/session.py`, `_validate_tool_result`; fastmcp's `Client.call_tool` and `call_tool_mcp` both route through it). The adapter opens a fresh session per call, so that was one hidden listing per successful call — in chat mode too — and, worse, a connector that does not serve `tools/list` would fail a call whose `tools/call` had succeeded. Fix, in this task: `mcp_adapter.call_tool` sends the `CallToolRequest` directly through the public `client.session.send_request(...)` (the same call the SDK's `call_tool` makes, minus the validation step) and `_extract_tool_payload` reads `isError` / `structuredContent` / `content` from the raw `mcp.types.CallToolResult`. The structured content is byte-identical to what fastmcp's wrapper exposed, so nothing chat mode reads changes; the wire cost drops by one request per call and the call no longer depends on `tools/list` being served. 09-11: reverted to the public `call_tool` — the per-session listing is accepted; only the pinning test was dropped.

**Files:**
- Modify: `deepagent-service/app/agent/connectors/mcp_adapter.py` (`call_tool` sends the request through `client.session.send_request`, `_extract_tool_payload` on the raw result)
- Test: `deepagent-service/tests/test_tool_call_endpoint.py`
- Docs (in Task 8): spec §4 row 4 → "dropped 09-10, see plan Task 3"; §10 item 2 → resolved; §2 model row unchanged (the model never learned about row 4).

- [x] **Step 1: tests** (`echo_server` wrapped in `RequestCountingMiddleware` exposing `counts[method]`):

| test | arrange | assert |
|---|---|---|
| `test_tool_call_unknown_tool_returns_tool_error_with_server_text` | `tool="no_such_tool"` | `TOOL_ERROR`; message contains `no_such_tool` and starts with `Unknown tool` (fastmcp's wording; if a fastmcp upgrade changes it, update the substring, never the code) — kept |
| `test_tool_call_never_calls_tools_list` | one successful `echo_tool` call, then one `no_such_tool` call | `counts.get("tools/list", 0) == 0`, `counts["tools/call"] == 2` — dropped 09-11 with the revert to the public `call_tool` (the per-session listing this test pinned against is now accepted) |

- [x] **Step 2: run** — the unknown-tool test passes as-is; the never-lists test fails until the adapter sends the request directly (see the finding above); make that change, then both pass and the whole suite stays green.
- [x] **Step 3: commit** — `fix(deepagent): tool calls send tools/call directly through the session — the SDK's call_tool lists tools per session for output-schema validation, one extra request per call and a hard failure when tools/list is not served; /tool-call pins unknown tool → TOOL_ERROR and never tools/list`

## Task 4: Contract fixture for Java and the frontend

**Files:**
- Create: `deepagent-service/tests/fixtures/mcp_result_examples.json`
- Create: `deepagent-service/tests/test_mcp_result_examples_fixture.py`

**Interfaces:** the JSON file is the shared artefact:

```json
{
  "success": {"data": {"result": [{"order_id": "A-1", "qty": 3}]}},
  "AUTH": {"error": {"code": "AUTH", "message": "connector 'sales' rejected your credentials (HTTP 401); sign in again"}},
  "RETRYABLE": {"error": {"code": "RETRYABLE", "message": "connector 'sales' did not respond (ConnectError) after 2 attempts; retry"}},
  "TOOL_ERROR": {"error": {"code": "TOOL_ERROR", "message": "unknown fab 'FAB_Z'; valid fab ids: FAB_A, FAB_B, FAB_C (call list_fabs)"}},
  "INVALID_CALL": {"error": {"code": "INVALID_CALL", "message": "args must be a JSON object, got list"}},
  "CONNECTOR_UNAVAILABLE": {"error": {"code": "CONNECTOR_UNAVAILABLE", "message": "tool 'list_orders' on connector 'sales' no longer returns structured data; ask the connector owner"}}
}
```

- [x] **Step 1: test** `tests/test_mcp_result_examples_fixture.py`:
  - `test_fixture_has_one_example_per_code_and_one_success`: keys == five codes + `success`.
  - `test_fixture_examples_have_exactly_one_of_data_or_error`: per example.
  - `test_fixture_error_messages_are_what_the_templates_produce`: rebuild each message by calling the `error_codes` template functions with the parameters the example encodes (`credentials_rejected("sales", 401)`, `no_response("sales", "ConnectError", 2)`, `tool_reported_error("get_quality", "unknown fab …")`, `args_not_object("list")`, `no_structured_data("sales", "list_orders")`) and assert byte equality — the fixture cannot drift from the code.
  - `test_fixture_codes_match_schema_literal`: every `code` validates through `ToolCallFailure`.
- [x] **Step 2: write the fixture; run; commit** — `test(deepagent): contract fixture mcp_result_examples.json pinned to the /tool-call templates`

Hand-off note (goes in the PR description): Java's `ArtifactController` proxy and the frontend bridge should load this file in their tests to check the fold table in spec §2 (`401/403/404 → AUTH`, `400/422 → INVALID_CALL`, `5xx/network → RETRYABLE`) and their display logic against the same strings.

## Task 5: Skill — `r.error.code`, the error branch, canonical `showCardError` / `showAuthBanner`, three page-contract sentences

**Files:**
- Modify: `deepagent-service/skills/mcp-data-dashboard/SKILL.md`
- Modify: `deepagent-service/tests/test_mcp_dashboard_skill_text.py`
- Verify only: `deepagent-service/app/agent/tools/check.py` (the `.error` presence lint and the forbidden-token lint must still pass on the new snippets)

**Text changes (all in English, model-facing):**

0. Frontmatter `description`: add "the five `r.error.code` values and what each means for the page (Retry only for `RETRYABLE`, one banner for `AUTH`)" to the list of what the file covers, so the skill index (what the model sees before it reads the file) already says error handling is specified here.
1. "Data contract -- `mcp()`", failure bullet: replace the parenthetical list with: `r.error.code` is one of `AUTH | RETRYABLE | TOOL_ERROR | INVALID_CALL | CONNECTOR_UNAVAILABLE`; `r.error.message` is a human-readable string; show `message` verbatim, never rewrite it; `r.data` is absent.
2. Same section, three new bullets (D9 page-facing proposals 2–4, now decided with this plan): the handler is always called asynchronously, never before `mcp()` returns; the runtime does not catch handler exceptions — they reach `window.onerror` and drive the repair flow; `toolArgs` must be JSON-serializable (`undefined` values are dropped, `NaN` becomes `null`).
3. "Reading the response": add the error branch right before the `const rows = …` bullet:

```js
if (r.error) {
  if (r.error.code === 'AUTH') showAuthBanner(r.error.message);
  else showCardError(card, r.error.message, r.error.code === 'RETRYABLE' ? retry : null);
  return;
}
```

4. "Card states": add `showCardError(card, message, retryFn)` and `showAuthBanner(message)` as canonical helpers built on the existing `setCardState`; the card template gains `<button data-slot="retry" class="hidden …">重試</button>`; `showAuthBanner` renders one idempotent page-level element (`#erd-auth-banner`, created once, text replaced). `retry` is a closure over the card's own loader (`const load = () => mcp('sales', 'list_orders', args, handler); const retry = load;`) so the connector and tool stay literal and `check_dashboard` still accepts the call. Note in prose: `RETRYABLE` → card error + Retry button; `AUTH` → banner, once, not per card; the other three → card error, no retry.
5. "Failure taxonomy" table: `r.error` row now says which codes show what; the "no rethrow" rule stays.
6. Handler skeleton and the two interactive examples (`if (r.error) {…}` in the skeleton, the two-step chain, the fan-out join): switch to the new branch shape.

- [x] **Step 1: tests** in `tests/test_mcp_dashboard_skill_text.py`:
  - `test_skill_description_mentions_error_codes`: the frontmatter block (text before the second `---`) contains `r.error.code`.
  - `test_skill_names_the_five_error_codes`: each name present, and the literal `r.error.code` present.
  - `test_skill_teaches_retry_only_for_retryable_and_banner_for_auth`: `"r.error.code === 'RETRYABLE'"` and `"r.error.code === 'AUTH'"` present; `"showCardError("` and `"showAuthBanner("` present.
  - `test_skill_states_handler_async_and_runtime_does_not_swallow_and_args_json`: the three sentences' key phrases.
  - `test_skill_snippets_pass_check_dashboard_contract_lint`: build a minimal dashboard HTML from the skill's new snippets (extract the fenced `js` blocks in "Card states" and "Reading the response") and run `_check_report(workspace, (connector,))` from `test_check_dashboard.py`'s helpers — no `forbidden` and no `contract` finding (the retry closure keeps literals; no `fetch`, no `window.parent`).
- [x] **Step 2: run, confirm failures; edit SKILL.md; run everything (ruff + pytest, `test_check_dashboard.py` included).**
- [x] **Step 3: commit** — `docs(deepagent): mcp-data-dashboard skill — r.error.code (five codes), Retry only for RETRYABLE, AUTH banner, handler async/exception/JSON-args sentences`

## Task 6: Landing feedback describes the handler argument, not "the raw response as r.data"

**Files:**
- Modify: `deepagent-service/app/agent/connectors/wrapper.py` (`describe_raw_response_shape`)
- Test: `deepagent-service/tests/test_connector_wrapper.py`

**Why:** the paragraph the model reads after every connector call currently says "mcp() hands your handler the raw response as r.data". That sentence is true but describes only the success half; the model never sees, at the moment it learns the data shape, that the argument is an envelope of its own (`{data: …}` or `{error: {code, message}}`), and it has been observed writing `r.result` / `r.data.data` guesses from exactly this gap. The feedback is the one place the model sees a concrete response, so the concrete handler argument belongs there too.

**Interfaces:** `describe_raw_response_shape(response, unwrap_path, envelope_fields, row_count) -> str` keeps its signature. Output gains one leading sentence and rewords the path sentence; the `Raw response shape:` prefix and the three canonical `r.data` / `r.data.result` / `r.data.data` paths stay (the skill and `test_mcp_dashboard_skill_text.py` refer to them):

```
Raw response shape: object with keys [result]. The table was built from response.result (an array of 700 objects); nothing else was dropped.
In the dashboard your handler receives r = {data: <this raw response>} on success or r = {error: {code, message}} on failure (never both) -- check r.error first, then read the rows with `r.data.result` -- not `r.data`.
```

Exact rules, one per existing branch of the function:
- array response: `… r = {data: <this raw response>} …, so r.data is already the array; read the rows with \`r.data\`.`
- non-envelope dict, landed as one row: `… r.data is that object; read fields directly (r.data.<first_key>).`
- non-envelope dict, empty: `… r.data is that object (empty).`
- envelope (any depth): the sentence above with `r.data.<path>`; the "Other fields beside the rows" sentence unchanged.
- 0-row envelope (`EmptyLandingError` branch in `_execute`): same wording; the shape text already reaches the model there.
- The failure half is one fixed clause, identical in every branch: `or r = {error: {code, message}} on failure (never both) -- check r.error first`. Do not list the five codes here (the skill owns that); the clause exists so the model knows `r` itself is the envelope.

- [x] **Step 1: tests** — in `tests/test_connector_wrapper.py`, for each of the five existing shape tests (lines ~456–543: `[result]`, array, `[data, errorCode]`, `[fab, yield]`, 0-row): keep the current assertions and add `assert "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure" in result` and `assert "check r.error first" in result`. Add `test_landing_feedback_never_says_handler_receives_raw_response_directly`: `"hands your handler the raw response as r.data" not in result` (the old wording is what is being retired).
- [x] **Step 2: run, confirm the new assertions fail; edit the four return branches; run `tests/test_connector_wrapper.py tests/test_mcp_dashboard_skill_text.py tests/test_chat_turn_connectors.py -q`, then the whole suite.**
- [x] **Step 3: commit** — `feat(deepagent): connector landing feedback states the handler argument shape — r = {data: raw} or {error: {code, message}}, check r.error first`

## Task 7: `mcp()` runtime prelude injected by `results.py` (hop ①)

**Files:**
- Modify: `deepagent-service/app/engine/results.py`
- Modify: `deepagent-service/app/agent/chat_turn.py`, `deepagent-service/app/agent/repair_flow.py`
- Modify: `deepagent-service/spike/mcp-shell/shell.html` (drop its own prelude; see Step 5)
- Test: `deepagent-service/tests/test_results.py`, `tests/test_mcp_runtime_prelude.py` (new), `tests/test_chat_turn_connectors.py`, `tests/test_repair.py`

**Interfaces:**

```python
# results.py (engine layer: stdlib only)
MCP_RUNTIME_SCRIPT_ID = "erd-mcp-runtime"
MCP_RUNTIME_VERSION = "1"
_INJECTED_SCRIPT_IDS = ("erd-results-data", MCP_RUNTIME_SCRIPT_ID)   # strip covers both


def build_mcp_runtime_script() -> str:
    """<script id="erd-mcp-runtime" data-erd-runtime="1"> defining window.mcp per the skill's
    data contract; the host half of the protocol lives in the frontend bridge."""


def inject_mcp_runtime(html: str) -> str:
    """Insert right after the opening <head …> tag (before any page script; Java's head-inject
    and the frontend CSP meta also insert there and are order-independent). No <head>: prepend."""


def has_mcp_runtime(html: str) -> bool
```

The prelude (the `<` inside must be written as `<` where it would close the script, same escaping rule as `build_results_script`):

```js
(function () {
  var nextCallId = 1;
  var pendingHandlersById = {};

  window.mcp = function (connectorName, toolName, toolArgs, handler) {
    var callId = String(nextCallId++);
    pendingHandlersById[callId] = handler;
    // JSON round-trip: undefined keys are dropped, NaN/Infinity become null — the same object
    // the MCP server will receive.
    var args = JSON.parse(JSON.stringify(toolArgs === undefined ? {} : toolArgs));
    parent.postMessage({ type: 'erd-mcp-call', id: callId, connector: connectorName, tool: toolName, args: args }, '*');
  };

  window.addEventListener('message', function (messageEvent) {
    var message = messageEvent.data;
    if (!message || message.type !== 'erd-mcp-result') return;
    var handler = pendingHandlersById[message.id];
    if (!handler) return;                       // late or unknown id: dropped (invariant 5)
    delete pendingHandlersById[message.id];
    var result = message.result;
    if (result && result.error && (result.error.code === 'TOOL_ERROR' || result.error.code === 'INVALID_CALL')) {
      parent.postMessage({ type: 'erd-artifact-error', errors: [{ message: 'mcp ' + result.error.code + ': ' + String(result.error.message).slice(0, 500), line: 0, col: 0 }] }, '*');
    }
    handler(result);                            // not wrapped: exceptions reach window.onerror (Java's relay)
  });
})();
```

Contract points the prelude must satisfy (they are the skill's sentences from Task 5): returns `undefined`; handler called exactly once per call, with one argument, always asynchronously (the postMessage round trip guarantees it); no try/catch around the handler; `args` JSON round-tripped; unknown or duplicate result ids ignored; no `window.onerror` of its own; the `erd-artifact-error` forward names the code and message but not argument values (the message text is the server's or ours, never the args — see Task 2 constraints).

Wiring:
- `chat_turn.py` at the point that calls `inject_results(themed_html, referenced_results)`: in connector mode (`connector_specs` non-empty) also `inject_mcp_runtime(...)`. File mode unchanged.
- `repair_flow.py`: `had_runtime = has_mcp_runtime(request.html)` before `strip_injected_blocks`; after the model's fix and `inject_results`, re-inject when `had_runtime`. When D10 adds `RepairRequest.connectors`, switch the condition to that and drop `had_runtime`.

- [x] **Step 1: tests**

`tests/test_results.py`:
- `test_build_mcp_runtime_script_carries_id_and_version_markers`: contains `id="erd-mcp-runtime"`, `data-erd-runtime="1"`, `window.mcp = function`, `'erd-mcp-call'`, `'erd-mcp-result'`, `'erd-artifact-error'`.
- `test_build_mcp_runtime_script_escapes_closing_tag`: no `</script>` inside the body except the real closing one (count == 1).
- `test_inject_mcp_runtime_after_head_open_tag_before_page_scripts`: for `<html><head><meta charset="utf-8"><script>page()</script></head>…`, the runtime block index is after `<head>` and before the page `<script>`.
- `test_inject_mcp_runtime_prepends_when_no_head`.
- `test_strip_injected_blocks_removes_mcp_runtime_and_results_blocks`: both ids gone; idempotent; `has_mcp_runtime` false after.
- `test_strip_then_inject_mcp_runtime_leaves_exactly_one_block`.

`tests/test_mcp_runtime_prelude.py` — behavioural test of the JS without a browser, using the `quickjs` package already declared in `pyproject.toml` (unused so far; if the binding is unavailable on a runner, mark these tests `importorskip("quickjs")` and keep the structural tests above as the floor):
- fixture: a `quickjs.Context` with a stub `window` (`addEventListener` storing the message listener), a stub `parent.postMessage` that appends to a `posted` array, and a `deliver(result_json_for_id)` helper that invokes the stored listener with `{data: {type: 'erd-mcp-result', id, result}}`; then `eval` the prelude body (extracted from `build_mcp_runtime_script()` between the script tags).
- `test_prelude_mcp_returns_undefined_and_posts_call_with_json_round_tripped_args`: `mcp('sales','list_orders',{days: 30, skip: undefined, ratio: NaN}, h)` → returns undefined; posted `erd-mcp-call` has `args == {"days": 30, "ratio": null}`, `id == "1"`.
- `test_prelude_handler_called_exactly_once_with_one_argument`: deliver the same id twice → handler count 1, argument equals the result.
- `test_prelude_ignores_unknown_result_id`: deliver id `"99"` → no handler call, no throw.
- `test_prelude_forwards_tool_error_and_invalid_call_to_artifact_error_channel_but_not_others`: deliver `{error:{code:'TOOL_ERROR',message:'x'}}` → one `erd-artifact-error` posted with `errors[0].message == "mcp TOOL_ERROR: x"`; `RETRYABLE`, `AUTH`, `CONNECTOR_UNAVAILABLE`, and `{data: …}` → none.
- `test_prelude_does_not_swallow_handler_exceptions`: handler throws → the `deliver` call raises (quickjs surfaces the JS exception), and the handler entry is already removed (a second deliver is ignored).
- `test_prelude_does_not_install_its_own_error_relay`: after eval, `window.onerror` is still undefined and no `'error'` listener was registered (the stub records listener types).

`tests/test_chat_turn_connectors.py`:
- `test_connector_mode_dashboard_html_event_carries_mcp_runtime_once`: fake model writes `dashboard.html`; the `DASHBOARD_HTML` event html has exactly one `id="erd-mcp-runtime"` and its `erd-results-data` block as before.
- `test_file_mode_dashboard_html_event_has_no_mcp_runtime`.
- `test_previous_dashboard_html_with_mcp_runtime_is_stripped_before_reaching_workspace`: `previousDashboardHtml` containing the block → workspace `dashboard.html` has no `erd-mcp-runtime`, so `check_dashboard` never lints the prelude (run `_check_report` on that file and assert no `forbidden` finding mentions `window.mcp =` or `postMessage(`).

`tests/test_repair.py`:
- `test_repair_reinjects_mcp_runtime_when_input_had_it` (exactly one block in the output; the model saw a clean input — assert the `HumanMessage` html lacks the id).
- `test_repair_does_not_add_mcp_runtime_when_input_lacked_it`.

- [x] **Step 2: run, confirm failures** — `uv run pytest tests/test_results.py tests/test_mcp_runtime_prelude.py tests/test_chat_turn_connectors.py tests/test_repair.py -q`.
- [x] **Step 3: implement `results.py`** (`build_mcp_runtime_script`, `inject_mcp_runtime`, `has_mcp_runtime`, extended `_INJECTED_SCRIPT_IDS`), then the two wirings. Keep `results.py` stdlib-only (ruff TID251 enforces it).
- [x] **Step 4: run everything** — ruff clean, pytest green; `tests/test_check_dashboard.py` untouched and green.
- [x] **Step 5: commit** — `feat(deepagent): mcp() runtime prelude injected by results.py in connector mode (erd-mcp-runtime block, stripped on iteration and repair)`. The spike side of this change is Task 9.

## Task 8: Wrap-up — docs, spec status, optional wrapper log line, gate

**Files:**
- Modify: `deepagent-service/README.md` (endpoint section next to `/chat` and `/repair`: path, headers, body, the two response shapes, "always 200 after auth", the five codes with the one-line who-acts table, pointer to the contract fixture)
- Modify: `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md`: status line → implemented on `feat/mcp-tool-call` (commit range); §4 row 4 → dropped (no `tools/list` at view time; unknown tool = server's `is_error` text under row 9; reasons in plan Task 3); §10 item 1 → resolved with what the transport surfaced (Task 2 Step 5); §10 item 2 → resolved by dropping row 4, Java allow-list (U5) remains the pre-call validation path.
- Modify: `docs/superpowers/specs/2026-09-09-mcp-dashboard-decision-summary.md` §3 "D9 傳輸面" row: deepagent hop ④ done, Java ③ and frontend ①② still zero code; §4 U2 → decided (the three sentences in the skill), U3 → deepagent part decided (pydantic schema + tests), the frontend/Java numbers still open.
- Modify: `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md`: §7 hop ① row and the "注入點" bullet → injected by deepagent `results.py` at generation time, connector mode only, block id `erd-mcp-runtime`, stripped on iteration and repair, version marker for a future serve-time replace; §13 decision record gets a 09-10 line ("hop ① moves from frontend srcdoc to deepagent generation time — team decision; file mode's results Proxy is the precedent; no other `mcp()` preamble exists in Java or the frontend; residual risk = untouched published dashboards keep their prelude version, escape hatch = strip-and-replace by id in `ArtifactAssembler`"). The frontend plan keeps hop ② only.
- Modify: `CLAUDE.md` status bullet for `feat/mcp-dashboard`: move "deepagent `/tool-call`" from 未落地 to 已落地 once merged.
- Optional, same commit: `app/agent/connectors/wrapper.py` `_execute` logs `tool_call connector=%s tool=%s arg_keys=%s ms=%d ok=%s code=%s` using `classify_connector_error(...).code` on the `ConnectorToolError` branch and `-` on success, so chat-mode and view-time calls grep together (spec §7). Skip it if Phase B's PR is already open (it edits the same function) and leave a note in the PR instead.

- [x] **Step 1: docs edits above.**
- [x] **Step 2: gate** — `cd deepagent-service && uv run ruff check . && uv run pytest -q` green; run `uv run pytest tests/test_connector_wrapper.py tests/test_chat_turn_connectors.py tests/test_check_dashboard.py -q` once more and paste the counts into the PR description.
- [x] **Step 3: opus full-branch review** (evidence-review skill), fix findings, "Ready to merge" written into the PR description, PR `feat/mcp-tool-call` → `feat/mcp-dashboard`. — Done 09-10: verdict Ready to merge (1 major: session-monitoring bypass made a 4xx/5xx on the `tools/call` POST wait out the read timeout — fixed in `6f16960` with a fail-fast test; 3 nits fixed, row-9 message cap recorded as a downstream decision in spec §10).
- [x] **Step 4: commit** — `docs: /tool-call documented; error-codes spec marked implemented; decision summary and CLAUDE.md status updated`

## Task 9: spike files updated to the new contract (prelude from deepagent, bridge through `/tool-call`, error paths visible)

The spike is the only host that can open a connector dashboard today, and it is also the D8 acceptance vehicle (re-run with a real model, replace the `out/` snapshots). After Tasks 2–7 its three files describe a contract that no longer exists: `shell.html` injects its own prelude (now injected by deepagent), `bridge.py` mirrors the adapter with a local `fastmcp.Client` (now `/tool-call` exists), and neither carries `code`. This task makes the spike an end-to-end check of hops ① and ④ with a browser in the loop. Throwaway code, no automated tests, but every item below has a visible acceptance step. Do it after Task 7.

**Files:** `spike/mcp-shell/shell.html`, `spike/mcp-shell/bridge.py`, `spike/mcp-shell/mock_server.py`, `spike/mcp-shell/README.md` (`generate.sh`, `run-deepagent.sh`, `skills/usage/SKILL.md` unchanged)

- [x] **`shell.html` (hop ② host half only):**
  - Delete `RUNTIME_PRELUDE` and the injection in `composeSrcdoc`; `composeSrcdoc` becomes identity plus a guard: if the loaded HTML lacks `id="erd-mcp-runtime"`, log `[shell] no erd-mcp-runtime block — this file predates Task 7, regenerate it` and still load it (so old snapshots in `out/` remain viewable, minus `mcp()`).
  - Accept `erd-mcp-call` only when `messageEvent.source === dashboardFrame.contentWindow` (what `ArtifactPanel` will do); ignore everything else silently.
  - Drop the `erd-iframe-error` handling (the prelude no longer sends it; Java's relay block, rendered by the bridge, already delivers `erd-artifact-error`).
  - Log line per call shows `code` on failure (`ERROR AUTH: …`) and arg **keys** only, not `JSON.stringify(message.args)`.
  - Host timeout: 60 s per call → post `{error: {code: 'RETRYABLE', message: 'host timeout after 60 s'}}` and drop a late result (invariant 5). Keep it simple: one `setTimeout` per call id.
- [x] **`bridge.py` (hop ③ stand-in):**
  - `/api/mcp/call` forwards to `POST {DEEPAGENT_URL}/tool-call` with `httpx.AsyncClient` (timeout 65 s), body `{"connector": {"id": "sales", "name": "sales-mock", "url": MOCK_MCP_URL}, "tool": …, "args": …}`, headers `Authorization: Bearer {AGENT_API_BEARER_TOKEN}` plus the two SSO headers from `Settings` with `DEV_SSO_TOKEN` / `DEV_SSO_URL` env values (same knobs `generate.sh` already uses; dummy defaults). `DEEPAGENT_URL` env, default `http://127.0.0.1:8000`.
  - Non-200 folds like the product bridge will: `401/403/404 → AUTH`, `400/422 → INVALID_CALL`, `5xx` and network failure → `RETRYABLE`, status in `message`; 200 passes through untouched (invariant 1).
  - Remove the `fastmcp.Client` / `StreamableHttpTransport` imports, the `_CONNECTORS` url map and the `_extract_tool_payload` mirror; the mock server url is only forwarded as `connector.url`.
  - Log arg keys only. `head-inject.vm` rendering, CDN rewrite for the internal runtime, `/api/dashboard` all stay.
- [x] **`mock_server.py`:** add three tools so every code can be provoked from a card without stopping servers: `slow_orders(days)` (sleeps `MOCK_SLOW_SECONDS`, default 35, past the 30 s adapter timeout → `RETRYABLE`), `orders_text_only()` (`output_schema=None`, returns a JSON string → no `structuredContent` → `CONNECTOR_UNAVAILABLE`), and keep `list_orders` raising `ToolError` for an unknown region (already there → `TOOL_ERROR`). `AUTH` is provoked by starting deepagent with a different `AGENT_API_BEARER_TOKEN` than the bridge; an unknown tool name is edited into `out/dashboard.html` by hand.
- [x] **`README.md`:** run order (mock server → `run-deepagent.sh` → `bridge.py` → `generate.sh`), where the prelude comes from now, and the acceptance list replacing the transport-side "not aligned" table: (1) a card with a misspelt tool shows a `TOOL_ERROR` card reading `Unknown tool: …`; (2) `slow_orders` card → `RETRYABLE` with a Retry button; (3) `orders_text_only` card → `CONNECTOR_UNAVAILABLE`, no Retry button; (4) bearer mismatch → one `AUTH` banner, not one per card; (5) the shell log shows `code` and arg keys, never values; (6) D8's three original acceptance points still hold on a fresh model run, and the `out/` snapshots are replaced (D8, plan A6 Step 2 of the autoland plan gets ticked there).
- [x] commit — `chore(deepagent): spike aligned with the transport contract — prelude from deepagent, bridge via POST /tool-call with non-200 folding, event.source check, host timeout, mock tools per error code, keys-only logs`

## Not in this plan (and where it lives)

| Item | Where decided | Why not here |
|---|---|---|
| Java `POST /api/artifacts/{id}/mcp-call` (ownership 404 → `AUTH`, connector ∉ `selectedConnectors` → `INVALID_CALL`, catalog lookup, SSO header forwarding via a shared extraction with `LangGraphAnalysisProvider`, `dataMode` on the artifact DTO) | D9 ③, spec §2 row "Java proxy" | separate Java plan; consumes Task 4's fixture |
| Frontend host bridge only (hop ②: `event.source` check, Java proxy call, `erd-mcp-result` post, in-flight cap 6 / 60 s timeout → `RETRYABLE`, non-200 folding). The prelude (hop ①) and the `TOOL_ERROR` / `INVALID_CALL` forward to `erd-artifact-error` are Task 7 here. | D9 ②, spec §2 row "Frontend bridge" | separate frontend plan; consumes Task 4's fixture |
| D10 repair prompt sentence — connector variant of `REPAIR_SYSTEM_PROMPT`: "`INVALID_CALL` and `TOOL_ERROR` are yours to fix; leave calls that failed with other codes unchanged." | main spec §6.2, error-codes spec §9 | D10 has no connector repair prompt yet; ship the sentence with D10 |
| `check_dashboard` warning when a handler's `r.error` branch never references `r.error.message` | error-codes spec §9 last line | Phase B or later (check.py is Phase B territory) |
| Rate limiting of view-time calls, per-artifact tool allow-list (`allowedCalls`), share-page access rules | D9 ③, U4–U6 | Java-side; observe per-artifact call rate first |
| A sixth code, or `HTTP_<status>` codes | 09-10 decision record | status goes in `message`; the page cannot act on it |

## Self-check against the spec

| spec section | covered by |
|---|---|
| §1 five codes, one remedy each; `{data}` xor `{error}` | Task 2 schemas (two models), `ErrorCode` literal |
| §2 what each audience knows | model: Task 5; Java/frontend: Task 4 fixture + §2 fold table in README; deepagent: Task 2 |
| §3 endpoint shape, bearer, SSO header names, always 200, 422 only for shape, no model/workspace/DuckDB/unwrap/log file | Task 2 route + `test_tool_call_never_unwraps_or_lands`, `test_tool_call_malformed_body_returns_422` |
| §4 rows 1–3, 5–11, first match wins, no secrets/values in `message` | Task 2; templates in `error_codes.py`; secrets test |
| §4 row 4 and the tool-name cache | dropped 09-10 (public `tools/list` not used); Task 3 pins unknown tool → `TOOL_ERROR` and `tools/list` never called; Task 8 writes it into the spec |
| §5 `kind` on `ConnectorToolError`, `_classify_cause`, retry skip on 401/403, chat mode unchanged | Task 1 |
| §6 not classified here (ownership, host timeout, budget) | "Not in this plan" |
| §7 log line, keys not values, traceback only on row 11 | Task 2 log line + tests; Task 8 optional wrapper line |
| §8 test table | Tasks 1–4 (every name present) |
| §9 skill changes | Task 5 (incl. frontmatter description); repair-prompt sentence deferred with D10 (listed) |
| §10 open items | item 1 resolved in Task 2 Step 5 and written back in Task 8; item 2 resolved by dropping row 4 (Task 3), allow-list stays Java's (U5); item 3 Java |
| D8 spike re-run and `out/` snapshots; D9 spike to-do list (code, `event.source`, timeout, non-200 folding, channel name, keys-only log, adapter reuse) | Task 9 |
| D9 hop ① (main spec §7: prelude signature, handler once, async, exceptions not swallowed, JSON args, injection point and condition) | "Decision" section + Task 7 prelude and tests; skill sentences in Task 5 |
| user additions 09-10: skill description for error handling; landing feedback states `{data}`/`{error}` shape; prelude in `results.py` | Task 5 item 0; Task 6; "Decision" section + Task 7 (prelude injected by deepagent, conditions 1–5, residual risk recorded) |
