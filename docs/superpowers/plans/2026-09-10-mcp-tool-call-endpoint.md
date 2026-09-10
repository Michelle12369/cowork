# deepagent `POST /tool-call` — MCP view-time endpoint with the five error codes — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship hop ④ of the D9 transport contract: a deepagent endpoint that a dashboard's view-time `mcp()` call reaches (through the frontend bridge and the Java proxy, both out of scope here), calls the MCP server through the same adapter path chat mode uses, and answers with exactly one of `{data: <raw structuredContent>}` or `{error: {code, message}}` where `code` is one of `AUTH | RETRYABLE | TOOL_ERROR | INVALID_CALL | CONNECTOR_UNAVAILABLE`. Every failure the adapter can produce is mapped to one of the five by the classification table in the spec (§4, rows 1–11), the mapping is pinned by tests, and the model learns the shape from both places it reads: the skill (five codes, `RETRYABLE` → Retry button, `AUTH` → page banner, everything else → card error with `message` verbatim) and the wrapper's landing feedback (the handler argument is `{data: <this raw response>}` or `{error: {code, message}}`, not the raw response itself). The plan also answers one placement question the user raised: whether the `mcp()` runtime prelude should be injected by deepagent in `results.py` — evaluated in the section before the tasks; recommendation is no.

**Architecture:** No model, no workspace, no DuckDB, no `unwrap_envelope`, no `connector_calls.jsonl`. `mcp_adapter.py` grows two async entry points (`list_tool_names`, `call_tool`) on top of the existing `_call`/`_run_with_retry`/`_extract_tool_payload`, and `ConnectorToolError` carries a `kind` (+ `status`, `attempts`, `cause_name`, `detail`) so the endpoint can classify without parsing message text. A new pure module `app/agent/connectors/error_codes.py` holds the five-code classifier and the message templates (the single place the spec §4 table lives in code); `app/agent/connectors/tool_call_flow.py` orders the checks (rows 1–4 before the call, 5–11 after) and owns the `tools/list` name cache; `app/main.py` adds the route next to `/chat` and `/repair` with the same bearer dependency and SSO header names. Chat mode keeps working unchanged: `_make_tool_call` becomes a thin sync wrapper over `call_tool`, all new `ConnectorToolError` attributes have defaults, and the wrapper still only reads `str(error)`.

**Tech Stack:** Python 3.11, FastAPI 0.141 (`Annotated` params, return-type response models, existing `RequireBearerToken` dependency), pydantic 2, fastmcp 3.x client (`StreamableHttpTransport`), httpx 0.28, pytest + pytest-asyncio (`asyncio_mode = "auto"`), ruff. Test MCP servers are real in-process `FastMCP` apps on random ports (pattern from `tests/test_mcp_adapter.py`).

**Status (2026-09-10):** plan written from the spec; nothing implemented. Branch for the work: `feat/mcp-tool-call` cut from `feat/mcp-dashboard`, merged back by PR (same route PR #81 took). Order relative to Phase B (`2026-09-08-mcp-dashboard-on-autoland.md` B1–B5) is still U1 in the decision summary; the two plans overlap in `skills/mcp-data-dashboard/SKILL.md` (different sections) and `wrapper.py` (Task 6 edits `describe_raw_response_shape`, Phase B's B2 edits `_execute`; Task 7's optional log line is in `_execute`) — whichever PR lands second rebases those two files.

**Spec:** `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md` (the full mapping; this plan implements all of §3–§9 and resolves the first two items of §10). Context: `2026-09-08-mcp-dashboard-on-autoland-design.md` §7 (D9 transport contract, five codes decided 09-10, invariants 1–5) and `2026-09-09-mcp-dashboard-decision-summary.md` §3–§4 (what is decided vs open).

## Glossary

| Term | Meaning | Where |
|---|---|---|
| **hop ④** | The deepagent leg of a view-time `mcp()` call: Java `POST /api/artifacts/{id}/mcp-call` → deepagent `POST /tool-call` → MCP server. Hops ①–③ (iframe runtime, host bridge, Java proxy) are separate plans. | `app/main.py`, `app/agent/connectors/tool_call_flow.py` |
| **code** | One of the five values in `r.error.code`. Cut by "who can act": viewer (`AUTH`, `RETRYABLE`), model/editor (`TOOL_ERROR`, `INVALID_CALL`), connector owner (`CONNECTOR_UNAVAILABLE`). Never a sixth value; HTTP status, hop, exception class all go into `message`. | `app/agent/connectors/error_codes.py` |
| **kind** | The adapter-level category on `ConnectorToolError`: `transport`, `http` (+ `status`), `tool`, `no_structured_content`, `config`. The classifier maps kind → code. Chat mode ignores it. | `app/agent/connectors/model.py` |
| **row N** | A row of spec §4's classification table. Tests are named after the code they pin, the plan text refers to rows. | spec §4 |
| **tool-name cache** | Per-`connector.url` set of tool names from `tools/list`, TTL `TOOL_CALL_TOOL_LIST_TTL_SECONDS` (default 60 s), so a six-card dashboard lists tools once per open, not six times. A miss on an unknown tool re-lists once before answering `INVALID_CALL`. | `tool_call_flow.py` (`ToolNameCache`) |
| **contract fixture** | `tests/fixtures/mcp_result_examples.json`: one realistic example response per code plus one success, kept byte-equal to what the templates produce. Java and the frontend load it to check their folding/display against the same strings. | Task 4 |

## Global Constraints

- Branch `feat/mcp-tool-call` from `feat/mcp-dashboard`; NEVER rebase or force-push once pushed; one commit per task; merge back via PR with `uv run ruff check .` clean, `uv run pytest -q` green, opus full-branch review "Ready to merge" in the PR description.
- Only `deepagent-service/` (incl. `skills/`, `spike/`) and `docs/superpowers/`. Zero Java, zero frontend. Where Java or the frontend must match a string or shape, this plan writes it into the contract fixture (Task 4) and the spec, nothing else.
- Endpoint invariants (spec §3, D9 invariants): always HTTP 200 once past bearer auth (422 only for pydantic shape failures); `data` is `structured_content` untouched; `args` are forwarded untouched; SSO only in headers, never in body, log, or `message`; exactly one of `data` / `error` in the body.
- `message` templates never include header values, bearer tokens, or argument **values**; argument **keys** are allowed. Exception text from the transport layer is never copied into `message` (class name only); the MCP server's own `is_error` text is copied verbatim (row 9) because that is the howto contract for actionable tool errors.
- Chat mode (`wrapper.py`, `check_dashboard`, landing, prompts) behaves identically before and after: the existing 487 tests stay green without edits other than the additive assertions this plan names.
- Same timeout and retry as chat mode (`CONNECTOR_REQUEST_TIMEOUT_SECONDS`, `CONNECTOR_CALL_RETRIES`); no extra overall deadline in the endpoint. Note for the frontend/Java plans: worst case per request is `(1 + retries) × timeout` for `tools/list` plus the same for `tools/call` (120 s at defaults), which is longer than the 60 s host timeout D9 suggests — the host's `RETRYABLE` on timeout is the intended behaviour, not a bug here.
- Names: no 1–2 character identifiers (`id` as a domain word is fine); loop counters `index`/`rowIndex`. Comments 1–2 lines, purpose + how, no spec numbers or commit hashes. Raise/log/model-facing text in English.
- Tests: `test_<subject>_<condition>_<expected>`; assert element-level behaviour (code, message substrings/regex, request counts), never whole-string snapshots; fixture servers are real `FastMCP` apps, monkeypatching `mcp_adapter.Client` only where a real server cannot produce the failure (row 11, secrets).
- `get_settings()` is an `lru_cache` singleton — any test that sets `CONNECTOR_*`/`TOOL_CALL_*` env vars relies on the global autouse cache reset in `conftest.py`; the tool-name cache gets its own autouse reset in the endpoint test module.

## File structure

| File | Task | Action | Responsibility |
|---|---|---|---|
| `app/agent/connectors/model.py` | 1 | modify | `ConnectorToolError(message, *, kind, status, attempts, cause_name, detail)` with defaults |
| `app/agent/connectors/mcp_adapter.py` | 1 | modify | `_classify_cause`, retry skip on 401/403, `kind` on every raise, new `list_tool_names` / `call_tool`, `_make_tool_call` delegates |
| `app/agent/connectors/error_codes.py` | 2 | new | `ErrorCode` literal, `ToolCallError` dataclass, message templates, `classify_connector_error(error, connector_id, tool_name)` |
| `app/agent/connectors/tool_call_flow.py` | 2, 3 | new | `execute_tool_call(request, sso_token, sso_url)`, rows 1–4 pre-checks, `ToolNameCache`, log line |
| `app/engine/request_context.py` | 2 | modify | `sso_identity(sso_token, sso_url)` context manager (sets only the two SSO contextvars) |
| `app/api/schemas.py` | 2 | modify | `ToolCallRequest`, `ToolCallErrorBody`, `ToolCallSuccess`, `ToolCallFailure` |
| `app/main.py` | 2 | modify | `POST /tool-call` route: bearer dependency, SSO headers, entry log |
| `app/config.py`, `one.properties` | 3 | modify | `TOOL_CALL_TOOL_LIST_TTL_SECONDS: float = 60.0` |
| `tests/mcp_fixture_servers.py` | 2 | new | `free_port`, `run_server_in_thread`, `ForcedStatusMiddleware`, `RequestCountingMiddleware` extracted from `test_mcp_adapter.py` |
| `tests/test_mcp_adapter.py` | 1, 2 | modify | additive `kind` assertions; import helpers from `mcp_fixture_servers` |
| `tests/test_mcp_adapter_retry.py` | 1 | modify | retry-skip and classification tests |
| `tests/test_tool_call_endpoint.py` | 2, 3 | new | the spec §8 table (rows 1–11, success, never-lands, secrets, log line, bearer 401) |
| `tests/fixtures/mcp_result_examples.json`, `tests/test_mcp_result_examples_fixture.py` | 4 | new | contract fixture + sync test |
| `skills/mcp-data-dashboard/SKILL.md`, `tests/test_mcp_dashboard_skill_text.py` | 5 | modify | frontmatter description; five codes, error branch, `showCardError` / `showAuthBanner`, three page-contract one-liners |
| `app/agent/connectors/wrapper.py`, `tests/test_connector_wrapper.py` | 6 | modify | landing feedback describes the handler argument (`{data: …}` / `{error: {code, message}}`) instead of "the raw response as r.data" |
| `README.md`, `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md`, `2026-09-09-mcp-dashboard-decision-summary.md`, `2026-09-08-mcp-dashboard-on-autoland-design.md`, `CLAUDE.md` | 7 | modify | endpoint documented; spec status → implemented; §10 items 1–2 resolved; summary §3 D9 row; prelude-placement evaluation recorded |
| `app/agent/connectors/wrapper.py` | 7 (optional) | modify | one `logger.info` line in the same `tool_call ...` format |
| `spike/mcp-shell/bridge.py`, `shell.html`, `README.md` | 8 (optional) | modify | bridge forwards to `/tool-call`, non-200 folding, `code` passthrough, keys-only log |

## Evaluation: should the `mcp()` runtime prelude live in `results.py`?

The question: `app/engine/results.py` already injects the file-mode preamble (`<script id="erd-results-data">` with `window.__ERD_RESULTS__` and the row Proxy) into the dashboard HTML at generation time (`chat_turn.py` before emitting `DASHBOARD_HTML`, `repair_flow.py` after a repair). The spike's `shell.html` injects the `mcp()` runtime prelude at srcdoc time instead. Could deepagent inject the prelude the same way it injects results, so every host (spike, product frontend) gets `mcp()` for free?

What is fixed today (D9, decision record 09-08): the prelude is injected by the **frontend** when it assembles the srcdoc, right after the CSP `<meta>`, only for connector-mode artifacts (`dataMode` from Java); the stored artifact stays the model's raw output. Java's `ArtifactAssembler` separately injects `head-inject.vm` (error relay → `erd-artifact-error`, Inter font, `erd` ECharts theme, `__ERD_DATA__`) at **serve** time, and `AgentConversationWriter` deliberately stores the clean base, not the assembled copy.

| Option | Who injects, when | "Fix the runtime once, all pages pick it up" | Stored HTML = model output | Spike cost | Notes |
|---|---|---|---|---|---|
| **A. Frontend at srcdoc time** (D9 as decided) | `ArtifactPanel`, per open | yes | yes | spike keeps its own `composeSrcdoc` (already exists) | prelude and host bridge (the two halves of the `erd-mcp-call`/`erd-mcp-result` protocol) live in one file, one release |
| **B. deepagent `results.py` at generation time** (the question) | `inject_results`-style block `<script id="erd-mcp-runtime">`, connector mode only, on every `DASHBOARD_HTML` and `/repair` | **no** — every stored dashboard carries the prelude version it was generated with; a protocol or bug fix means regenerating or rewriting artifacts under 2-year retention, or a Java strip-and-replace pass at serve time (which is option C with extra steps) | no — `previousDashboardHtml` fed back to the model and the Java "clean base" would need `strip_injected_blocks` on one more id; workable (`_INJECTED_SCRIPT_IDS` exists for this) but one more thing to keep stripping | zero: `composeSrcdoc` becomes a no-op | splits one postMessage protocol across two repos: iframe half in deepagent, host half in the frontend; a change to either needs a coordinated release plus artifact migration |
| **C. Java `head-inject.vm` at serve time** | `ArtifactAssembler`, `#if($connectorMode)` next to `#if($hasEcharts)` | yes | yes | zero-ish: the spike bridge already renders `head-inject.vm` (commit `c40d24d`) so it would get the prelude by re-rendering | same "fix once" property as A; Java already knows `dataMode` (session → `selectedConnectors`), so the frontend would need no prelude logic at all; the prelude would sit beside the error relay it cooperates with (D10 uses `erd-artifact-error`) |

**Recommendation: do not put the prelude in `results.py`.** Generation-time injection freezes a host-runtime concern into each artifact, which is exactly what the 09-08 decision avoided ("runtime fix once, every published page gets it next open"), and it moves half of a frontend protocol into the Python service. The one thing B buys — the spike needing no `composeSrcdoc` — is not worth it for a throwaway host, and C buys the same for the spike without the freeze.

If the user wants a single injection point that also serves the spike, the candidate is **C, not B**: amend D9 so hop ① is injected by Java's `head-inject.vm` at serve time (condition `connectorMode`), the frontend drops the prelude work, and the spike bridge picks it up by re-rendering the template it already renders. That is a D9 amendment for the Java/frontend plans and needs the user's call; it does not change anything in this plan (hop ④ is the same either way). Recorded in Task 7 as an open item in the main spec §7 rather than decided here.

What `results.py` can still do for connector mode, independent of the prelude: skip the empty `__ERD_RESULTS__` block when no `qN` is referenced (U11) — harmless today, not in this plan.

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
        cause_name: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status          # HTTP status when kind == "http"
        self.attempts = attempts      # how many attempts _call made before giving up
        self.cause_name = cause_name  # class name of the transport cause (never its text)
        self.detail = detail          # the MCP server's own error text when kind == "tool"
```

```python
# mcp_adapter.py — new public entry points (async; chat mode's _make_tool_call wraps call_tool)
async def list_tool_names(connector_id: str, base_url: str, bearer_token: str | None) -> frozenset[str]
async def call_tool(connector_id: str, base_url: str, tool_name: str, args: dict, bearer_token: str | None) -> object
```

- `_classify_cause(raised: BaseException) -> tuple[ConnectorToolErrorKind, int | None, str]` walks `__cause__` / `__context__` and, for `BaseExceptionGroup`, `.exceptions` (fastmcp wraps connect failures in `RuntimeError("Client failed to connect: …") from exception`, and anyio task groups can surface groups). First match wins: `httpx.HTTPStatusError` → `("http", response.status_code, "HTTPStatusError")`; `TimeoutError` (which is `asyncio.TimeoutError` on 3.11), `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.RemoteProtocolError`, any other `httpx.TransportError`, `McpError` → `("transport", None, <that class name>)`; nothing matched → `("transport", None, type(raised).__name__)`.
- `_run_with_retry` gives up immediately (no retry, one warning log with the status) when `_classify_cause` says `http` with status 401 or 403. Everything else keeps "any exception is retried".
- `_call` builds `ConnectorToolError(_actionable_message(...), kind=kind, status=status, cause_name=cause_name, attempts=attempt_count)` where `attempt_count` is 1 for the 401/403 short-circuit and `1 + max(0, CONNECTOR_CALL_RETRIES)` otherwise (deterministic because the short-circuit is the only early exit).
- `_extract_tool_payload`: `is_error` → `kind="tool", detail=<server text or None>`; `structured_content is None` → `kind="no_structured_content"`. Messages unchanged.
- `load_mcp_connector` missing bearer key → `kind="config"`. Message unchanged.

- [ ] **Step 1: write the failing tests**

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

New tests in `test_mcp_adapter.py` for the two entry points (echo_server fixture):

```python
async def test_list_tool_names_returns_fixture_tool_names(echo_server) -> None:
    with _identity():
        names = await list_tool_names("fixture", echo_server["base_url"], None)
    assert {"echo_tool", "text_only_echo_tool", "failing_tool"} <= names


async def test_call_tool_returns_structured_content_unchanged(echo_server) -> None:
    with _identity():
        payload = await call_tool("fixture", echo_server["base_url"], "echo_tool", {"message": "hi"}, None)
    assert payload == {"echo": "hi"}
```

- [ ] **Step 2: run, confirm failures**

Run: `cd deepagent-service && uv run pytest tests/test_mcp_adapter_retry.py tests/test_mcp_adapter.py -q`
Expected: new tests FAIL (`TypeError` on kwargs / `AttributeError: kind` / `ImportError` for the entry points); existing ones PASS.

- [ ] **Step 3: implement `model.py` and `mcp_adapter.py`**

1. `model.py` as in Interfaces. Keep the class docstring to two lines.
2. `mcp_adapter.py`:
   - `_TRANSPORT_CAUSE_TYPES: tuple[type[BaseException], ...] = (TimeoutError, httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.TransportError, McpError)`; import `httpx` and `from mcp.shared.exceptions import McpError`.
   - `_iter_cause_chain(raised)` generator: yields `raised`, then recurses into `.exceptions` for `BaseExceptionGroup`, then `__cause__`, then `__context__` (guard against cycles with an `id()` set).
   - `_classify_cause` as specified.
   - `_run_with_retry`: inside `except`, compute `kind, status, _ = _classify_cause(raised_exception)`; if `kind == "http" and status in _REJECTED_CREDENTIAL_STATUSES` → warning log `"MCP call rejected credentials: connector=%s method=%s url=%s status=%d, not retrying"` and `raise`. Keep the existing final-attempt log otherwise.
   - `_call`: compute `max_attempt_count` the same way `_run_with_retry` does (extract `_max_attempt_count()` helper used by both), classify, and raise with all attributes.
   - `list_tool_names` = `_call(..., "tools/list", _build_headers(bearer_token), lambda client: client.list_tools())` → `frozenset(tool.name for tool in tools)`.
   - `call_tool` = `_call(..., "tools/call", headers, lambda client: client.call_tool(tool_name, args, raise_on_error=False))` → `_extract_tool_payload(result, tool_name, connector_id)`.
   - `_make_tool_call` → `def call(args): return asyncio.run(call_tool(connector_id, base_url, tool_name, args, bearer_token))`.
   - `_extract_tool_payload`: pass `kind="tool", detail=error_text or None` and `kind="no_structured_content"`.
   - `load_mcp_connector`: `kind="config"` on the missing-bearer raise.

- [ ] **Step 4: run the whole suite**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: all green, 487 + the new tests. The retry tests that rely on "any exception is retried once" still pass because none of them uses 401/403.

- [ ] **Step 5: commit**

`feat(deepagent): ConnectorToolError carries kind/status/attempts; adapter classifies causes, skips retry on 401/403, exposes list_tool_names/call_tool`

## Task 2: `POST /tool-call` — schemas, SSO context, classifier, flow, route (rows 1–3 and 5–11)

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
    tool: str                          # empty string is a row-3 INVALID_CALL, not a 422
    args: Any                          # non-object is a row-3 INVALID_CALL, not a 422 (spec §4 row 3)


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
def unknown_tool(connector_id: str, tool: str, available: Iterable[str]) -> ToolCallError  # row 4
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
| 4 | `tool '<tool>' does not exist on connector '<id>'; available: <names sorted, comma-separated>` |
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
    """Rows 1–4 before touching the network, then one tools/call, then rows 5–11. Always returns;
    never raises past the row-11 fallback. Logs one `tool_call ...` line per call."""
```

Order inside (spec: first matching row wins, numeric order):
1. Row 1: for `(settings.SSO_TOKEN_HEADER, sso_token), (settings.SSO_URL_HEADER, sso_url)` the first empty one → `missing_sso_header(name)`.
2. Row 2: `connector.bearerTokenKey` set and `connector_bearer_token(key) is None` → row 2.
3. Row 3: `not request.tool.strip()` → `empty_tool_name()`; `not isinstance(request.args, dict)` → `args_not_object(type(request.args).__name__)`.
4. Row 4: Task 3 (tool-name cache). Task 2 leaves a one-line hook that calls `list_tool_names` directly with no cache so the row-4 happy path already works; Task 3 replaces it.
5. `with sso_identity(sso_token, sso_url): payload = await call_tool(...)` → `ToolCallSuccess(data=payload)`.
6. `except ConnectorToolError as error` → `classify_connector_error(error, connector.id, tool)` (rows 2, 5–10; note `tools/list` failures in step 4 flow through the same except).
7. `except Exception as error` → `logger.exception("tool_call unexpected failure connector=%s tool=%s", ...)` + row 11.
8. Log line (spec §7) in a `finally`-style wrapper around 1–7: `tool_call connector=<id> tool=<tool> arg_keys=[k1,k2] ms=<n> ok=true|false code=<code or ->` — `arg_keys` from `sorted(request.args)` when it is a dict, `[]` otherwise; on success `code=-`.

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

- [ ] **Step 1: extract fixture-server helpers**

Create `tests/mcp_fixture_servers.py` with `free_port()`, `run_server_in_thread(app, port) -> uvicorn.Server`, `ForcedStatusMiddleware`, `HeaderCapturingMiddleware` (moved verbatim from `test_mcp_adapter.py`, underscore prefix dropped), plus a new `RequestCountingMiddleware` (counts HTTP requests by JSON-RPC `method`; used by Task 3 to count `tools/list`). `test_mcp_adapter.py` imports them; its fixtures stay where they are. Run `uv run pytest tests/test_mcp_adapter.py -q` — still green.

- [ ] **Step 2: write the failing endpoint tests**

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
| `test_tool_call_empty_tool_name_returns_invalid_call` | `tool=""` | `INVALID_CALL`, `message == "tool name is empty"` |
| `test_tool_call_non_object_args_returns_invalid_call` | `args=[1, 2]` | `INVALID_CALL`, `message == "args must be a JSON object, got list"` |
| `test_tool_call_timeout_returns_retryable_after_configured_retries` | `slow_tool`, timeout 0.3 s, retries 1 | `RETRYABLE`, message matches `r"connector 'fixture' did not respond \(\w+\) after 2 attempts; retry"` |
| `test_tool_call_connection_refused_returns_retryable` | `unreachable_url` | `RETRYABLE`, message contains `did not respond (` and `; retry` |
| `test_tool_call_http_401_returns_auth_without_retry` | `status_server(401)`, retries 1, `RequestCountingMiddleware` | `AUTH`, message `== "connector 'fixture' rejected your credentials (HTTP 401); sign in again"`, request count equals the count observed with retries 0 (measure both in the test) |
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

- [ ] **Step 3: run, confirm failures**

Run: `cd deepagent-service && uv run pytest tests/test_tool_call_endpoint.py -q`
Expected: all FAIL with 404 from the missing route.

- [ ] **Step 4: implement**

1. `request_context.sso_identity` (set both vars, `try/finally` reset).
2. `schemas.py` models; extend the module docstring to name the third endpoint.
3. `error_codes.py` exactly as in Interfaces. Row 2 needs the key: in Task 1's `load_mcp_connector` raise, also pass `detail=bearer_token_key` (one-line addition; keep Task 1's test green).
4. `tool_call_flow.py`: `execute_tool_call` with the ordering above; row-4 hook calls `list_tool_names` uncached for now (`_tool_exists`).
5. `main.py` route; add `ToolCallRequest`, `ToolCallSuccess`, `ToolCallFailure` to the `__all__` re-export list only if a test needs them (it does not; skip).

- [ ] **Step 5: verify spec §10 item 1 against the real transport**

While the 401/404/503 tests run against `ForcedStatusMiddleware`, confirm the status reaches `ConnectorToolError.status` through fastmcp's cause chain (the Task 1 additive assertion on the 401 test already proves it for `tools/list`; the endpoint tests prove it for `tools/call`). If 404 turns out to be swallowed by the mcp client into a generic error (it has special handling for expired sessions), do **not** loosen the test: record what the transport actually surfaces in spec §10 and change the expected message to spec's stated fallback (`RETRYABLE` with the status in `message`) only with that note written first.

- [ ] **Step 6: run everything**

Run: `cd deepagent-service && uv run ruff check . && uv run pytest -q`
Expected: green. Check `tests/test_api_auth.py` still passes — the new route uses the same dependency.

- [ ] **Step 7: commit**

`feat(deepagent): POST /tool-call — view-time MCP call with the five error codes (rows 1–3, 5–11), no unwrap, no landing`

## Task 3: Row 4 — tool existence check with a TTL cache of `tools/list`

**Files:**
- Modify: `deepagent-service/app/config.py`, `deepagent-service/one.properties`
- Modify: `deepagent-service/app/agent/connectors/tool_call_flow.py`
- Test: `deepagent-service/tests/test_tool_call_endpoint.py`, `deepagent-service/tests/test_config.py`

**Interfaces:**

```python
# config.py
# View-time /tool-call caches each connector's tool names (tools/list) for this many seconds so
# a dashboard with several cards lists tools once per open. 0 disables the cache.
TOOL_CALL_TOOL_LIST_TTL_SECONDS: float = 60.0
```

```python
# tool_call_flow.py
class ToolNameCache:
    """non-bean: one module-level instance; keyed by connector url; TTL from settings.
    names_for() returns (names, from_cache) so the caller can re-list once on a miss."""

    async def names_for(self, connector: ConnectorSpec, bearer_token: str | None, *, refresh: bool = False) -> tuple[frozenset[str], bool]
    def clear(self) -> None


_tool_name_cache = ToolNameCache()
```

Row-4 logic in `execute_tool_call`: `names, from_cache = await cache.names_for(connector, bearer_token)`; if `tool not in names and from_cache`: `names, _ = await cache.names_for(connector, bearer_token, refresh=True)`; if still not in `names` → `unknown_tool(connector.id, tool, sorted(names))`. A `tools/list` failure raises `ConnectorToolError` and lands in the same `except` as the call (rows 5–8), which is the right answer for the viewer (a server that cannot list tools cannot serve the call either).

Known limitation to write into the module docstring: the cache is per url, not per viewer; if a server filters `tools/list` by identity, the `available: …` list in a row-4 message can name tools another viewer listed. Names only, never data; accepted for v1 (spec §10 item 2 leaves the alternative — Java passing an allow-list — for later).

- [ ] **Step 1: tests**

`tests/test_config.py`: `test_settings_tool_call_tool_list_ttl_defaults_to_sixty_seconds` and an env override test (pattern of the neighbouring `CONNECTOR_*` tests).

`tests/test_tool_call_endpoint.py` (autouse fixture `_clear_tool_name_cache` calls `tool_call_flow._tool_name_cache.clear()` before and after each test; `echo_server` wrapped in `RequestCountingMiddleware` exposing `counts["tools/list"]`):

| test | arrange | assert |
|---|---|---|
| `test_tool_call_unknown_tool_returns_invalid_call_listing_available_tools` | `tool="no_such_tool"` | `INVALID_CALL`, message `== "tool 'no_such_tool' does not exist on connector 'fixture'; available: echo_tool, failing_tool, list_tool, slow_tool, text_only_tool"` |
| `test_tool_call_known_tool_within_ttl_lists_tools_once` | two successful `echo_tool` calls | `counts["tools/list"] == 1` |
| `test_tool_call_unknown_tool_relists_once_before_invalid_call` | warm the cache with one `echo_tool` call, then `no_such_tool` | list count went from 1 to 2 (exactly one re-list), `INVALID_CALL` |
| `test_tool_call_newly_added_tool_is_found_by_relist` | warm cache; then register a new tool on the running `FastMCP` instance (`mcp_server.tool()(new_fn)`); call it | success (the re-list saw it), `counts["tools/list"] == 2` |
| `test_tool_call_tool_list_cache_expires_after_ttl` | `TOOL_CALL_TOOL_LIST_TTL_SECONDS=0.05`; two calls with `await asyncio.sleep(0.1)` between | `counts["tools/list"] == 2` |
| `test_tool_call_tool_list_ttl_zero_disables_cache` | TTL `0`; two calls | `counts["tools/list"] == 2` |
| `test_tool_call_tools_list_failure_is_classified_like_the_call` | `status_server(503)` (fails at list time) | `RETRYABLE` with `HTTP 503` — already what Task 2's 503 test observes; keep it as an explicit name |

- [ ] **Step 2: run, confirm failures** (`counts` assertions fail; the unknown-tool message already passes from Task 2's uncached hook — that is fine, keep the test).

- [ ] **Step 3: implement** `ToolNameCache` with `time.monotonic()` expiry, `refresh=True` bypassing the entry, and the flow change. `one.properties` gets the key under a new `# ── view-time /tool-call ──` heading with the two-line explanation.

- [ ] **Step 4: run everything** — ruff clean, pytest green.

- [ ] **Step 5: commit** — `feat(deepagent): /tool-call checks the tool exists (row 4) with a per-url tools/list cache, re-listing once on a miss`

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
  "INVALID_CALL": {"error": {"code": "INVALID_CALL", "message": "tool 'list_order' does not exist on connector 'sales'; available: list_orders, list_regions"}},
  "CONNECTOR_UNAVAILABLE": {"error": {"code": "CONNECTOR_UNAVAILABLE", "message": "tool 'list_orders' on connector 'sales' no longer returns structured data; ask the connector owner"}}
}
```

- [ ] **Step 1: test** `tests/test_mcp_result_examples_fixture.py`:
  - `test_fixture_has_one_example_per_code_and_one_success`: keys == five codes + `success`.
  - `test_fixture_examples_have_exactly_one_of_data_or_error`: per example.
  - `test_fixture_error_messages_are_what_the_templates_produce`: rebuild each message by calling the `error_codes` template functions with the parameters the example encodes (`credentials_rejected("sales", 401)`, `no_response("sales", "ConnectError", 2)`, `tool_reported_error("get_quality", "unknown fab …")`, `unknown_tool("sales", "list_order", ["list_orders", "list_regions"])`, `no_structured_data("sales", "list_orders")`) and assert byte equality — the fixture cannot drift from the code.
  - `test_fixture_codes_match_schema_literal`: every `code` validates through `ToolCallFailure`.
- [ ] **Step 2: write the fixture; run; commit** — `test(deepagent): contract fixture mcp_result_examples.json pinned to the /tool-call templates`

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

- [ ] **Step 1: tests** in `tests/test_mcp_dashboard_skill_text.py`:
  - `test_skill_description_mentions_error_codes`: the frontmatter block (text before the second `---`) contains `r.error.code`.
  - `test_skill_names_the_five_error_codes`: each name present, and the literal `r.error.code` present.
  - `test_skill_teaches_retry_only_for_retryable_and_banner_for_auth`: `"r.error.code === 'RETRYABLE'"` and `"r.error.code === 'AUTH'"` present; `"showCardError("` and `"showAuthBanner("` present.
  - `test_skill_states_handler_async_and_runtime_does_not_swallow_and_args_json`: the three sentences' key phrases.
  - `test_skill_snippets_pass_check_dashboard_contract_lint`: build a minimal dashboard HTML from the skill's new snippets (extract the fenced `js` blocks in "Card states" and "Reading the response") and run `_check_report(workspace, (connector,))` from `test_check_dashboard.py`'s helpers — no `forbidden` and no `contract` finding (the retry closure keeps literals; no `fetch`, no `window.parent`).
- [ ] **Step 2: run, confirm failures; edit SKILL.md; run everything (ruff + pytest, `test_check_dashboard.py` included).**
- [ ] **Step 3: commit** — `docs(deepagent): mcp-data-dashboard skill — r.error.code (five codes), Retry only for RETRYABLE, AUTH banner, handler async/exception/JSON-args sentences`

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

- [ ] **Step 1: tests** — in `tests/test_connector_wrapper.py`, for each of the five existing shape tests (lines ~456–543: `[result]`, array, `[data, errorCode]`, `[fab, yield]`, 0-row): keep the current assertions and add `assert "r = {data: <this raw response>} on success or r = {error: {code, message}} on failure" in result` and `assert "check r.error first" in result`. Add `test_landing_feedback_never_says_handler_receives_raw_response_directly`: `"hands your handler the raw response as r.data" not in result` (the old wording is what is being retired).
- [ ] **Step 2: run, confirm the new assertions fail; edit the four return branches; run `tests/test_connector_wrapper.py tests/test_mcp_dashboard_skill_text.py tests/test_chat_turn_connectors.py -q`, then the whole suite.**
- [ ] **Step 3: commit** — `feat(deepagent): connector landing feedback states the handler argument shape — r = {data: raw} or {error: {code, message}}, check r.error first`

## Task 7: Wrap-up — docs, spec status, optional wrapper log line, gate

**Files:**
- Modify: `deepagent-service/README.md` (endpoint section next to `/chat` and `/repair`: path, headers, body, the two response shapes, "always 200 after auth", the five codes with the one-line who-acts table, pointer to the contract fixture)
- Modify: `docs/superpowers/specs/2026-09-10-mcp-error-codes-design.md`: status line → implemented on `feat/mcp-tool-call` (commit range); §10 item 1 → resolved with what the transport surfaced (Task 2 Step 5); §10 item 2 → cache shipped with TTL setting, allow-list still open.
- Modify: `docs/superpowers/specs/2026-09-09-mcp-dashboard-decision-summary.md` §3 "D9 傳輸面" row: deepagent hop ④ done, Java ③ and frontend ①② still zero code; §4 U2 → decided (the three sentences in the skill), U3 → deepagent part decided (pydantic schema + tests), the frontend/Java numbers still open.
- Modify: `docs/superpowers/specs/2026-09-08-mcp-dashboard-on-autoland-design.md` §7 "本 spec 凍結／留給實作 plan 的分界": add one open item "hop ① injection point: frontend srcdoc (as decided) vs Java `head-inject.vm` at serve time (option C in the plan's evaluation); generation-time injection in deepagent `results.py` evaluated and rejected" so the Java/frontend plans see it.
- Modify: `CLAUDE.md` status bullet for `feat/mcp-dashboard`: move "deepagent `/tool-call`" from 未落地 to 已落地 once merged.
- Optional, same commit: `app/agent/connectors/wrapper.py` `_execute` logs `tool_call connector=%s tool=%s arg_keys=%s ms=%d ok=%s code=%s` using `classify_connector_error(...).code` on the `ConnectorToolError` branch and `-` on success, so chat-mode and view-time calls grep together (spec §7). Skip it if Phase B's PR is already open (it edits the same function) and leave a note in the PR instead.

- [ ] **Step 1: docs edits above.**
- [ ] **Step 2: gate** — `cd deepagent-service && uv run ruff check . && uv run pytest -q` green; run `uv run pytest tests/test_connector_wrapper.py tests/test_chat_turn_connectors.py tests/test_check_dashboard.py -q` once more and paste the counts into the PR description.
- [ ] **Step 3: opus full-branch review** (evidence-review skill), fix findings, "Ready to merge" written into the PR description, PR `feat/mcp-tool-call` → `feat/mcp-dashboard`.
- [ ] **Step 4: commit** — `docs: /tool-call documented; error-codes spec marked implemented; decision summary and CLAUDE.md status updated`

## Task 8 (optional, recommended): spike bridge goes through the real endpoint

The spike is the only host that can open a connector dashboard today. Pointing it at `/tool-call` turns it into an end-to-end check of hop ④ with a browser in the loop, and covers the "bridge.py re-implements the adapter" item of the D9 spike to-do list. Throwaway code; no automated tests; do it after Task 6 so the skill's Retry/banner snippets and the new feedback wording can be seen working together on a real model run.

**Files:** `spike/mcp-shell/bridge.py`, `spike/mcp-shell/shell.html`, `spike/mcp-shell/README.md`

- [ ] `bridge.py` `/mcp-call`: instead of opening a `fastmcp.Client`, `httpx.AsyncClient.post("http://127.0.0.1:8000/tool-call", json={"connector": {...}, "tool": ..., "args": ...}, headers={"Authorization": f"Bearer {settings.AGENT_API_BEARER_TOKEN}", SSO_TOKEN_HEADER: "spike", SSO_URL_HEADER: "http://spike.invalid"})`. Non-200 folds like the frontend bridge will (`401/403/404 → AUTH`, `400/422 → INVALID_CALL`, `5xx`/network → `RETRYABLE`, status in `message`) so the page sees one shape. Log arg keys only. Drop the local `fastmcp` client and `_extract_tool_payload` mirror.
- [ ] `shell.html` prelude: pass `result` through unchanged (it already does); no more local `{error:{message}}` synthesis without `code`.
- [ ] README: the run order (mock server → deepagent → bridge), and an acceptance list: (1) a card whose tool name is misspelt shows an `INVALID_CALL` card with the available names; (2) stop the mock server → `RETRYABLE` card with a working Retry button; (3) start it again, click Retry → data; (4) `AGENT_API_BEARER_TOKEN` mismatch → `AUTH` banner once, not per card.
- [ ] commit — `chore(deepagent): spike bridge calls POST /tool-call instead of mirroring the adapter`

## Not in this plan (and where it lives)

| Item | Where decided | Why not here |
|---|---|---|
| Java `POST /api/artifacts/{id}/mcp-call` (ownership 404 → `AUTH`, connector ∉ `selectedConnectors` → `INVALID_CALL`, catalog lookup, SSO header forwarding via a shared extraction with `LangGraphAnalysisProvider`, `dataMode` on the artifact DTO) | D9 ③, spec §2 row "Java proxy" | separate Java plan; consumes Task 4's fixture |
| Frontend prelude + host bridge (`erd-mcp-call` / `erd-mcp-result`, `event.source` check, in-flight cap 6 / 60 s timeout → `RETRYABLE`, non-200 folding, forward only `TOOL_ERROR` / `INVALID_CALL` to `erd-artifact-error`) | D9 ①②, spec §2 row "Frontend bridge" | separate frontend plan; consumes Task 4's fixture |
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
| §4 rows 1–11, first match wins, no secrets/values in `message` | Task 2 (1–3, 5–11), Task 3 (4); templates in `error_codes.py`; secrets test |
| §4 tool-name cache, re-list once on a miss | Task 3 |
| §5 `kind` on `ConnectorToolError`, `_classify_cause`, retry skip on 401/403, chat mode unchanged | Task 1 |
| §6 not classified here (ownership, host timeout, budget) | "Not in this plan" |
| §7 log line, keys not values, traceback only on row 11 | Task 2 log line + tests; Task 7 optional wrapper line |
| §8 test table | Tasks 1–4 (every name present) |
| §9 skill changes | Task 5 (incl. frontmatter description); repair-prompt sentence deferred with D10 (listed) |
| §10 open items | item 1 resolved in Task 2 Step 5 and written back in Task 7; item 2 cache shipped, allow-list stays open; item 3 Java |
| user additions 09-10: skill description for error handling; landing feedback states `{data}`/`{error}` shape; prelude-in-`results.py` viability | Task 5 item 0; Task 6; "Evaluation" section (recommendation: no; option C if one injection point is wanted) |
