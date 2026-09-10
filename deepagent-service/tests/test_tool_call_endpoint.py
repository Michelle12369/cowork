"""`/tool-call` 端點測試——view-time MCP 呼叫的五個錯誤 code 與成功路徑(spec §8 表列的每一
種情境),用真的本地 fastmcp v3 fixture server 覆蓋 rows 1-3、5-11(row 4 由 plan Task 3 另外
覆蓋,這裡不碰)。永遠 HTTP 200(bearer/pydantic 失敗除外),body 恰好 `{"data": ...}` 或
`{"error": {"code", "message"}}` 其中一種。"""

import re
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.agent.connectors import mcp_adapter, tool_call_flow
from app.config import get_settings
from app.engine import api_snapshot
from tests.conftest import TEST_BEARER_TOKEN
from tests.mcp_fixture_servers import (
    ForcedStatusMiddleware,
    RequestCountingMiddleware,
    free_port,
    run_server_in_thread,
)

_SSO_HEADERS = {
    "X-SSO-Token": "sso-token-value-not-in-messages",
    "X-SSO-Url": "https://sso.test.example/auth",
}
_FAILING_TOOL_MESSAGE = "upstream tool failed for tool-call contract test"
_UNREACHABLE_URL = "http://127.0.0.1:1/mcp"


def _connector(base_url: str, bearer_token_key: str | None = None) -> dict[str, Any]:
    return {
        "id": "fixture",
        "name": "Fixture Server",
        "url": base_url,
        "bearerTokenKey": bearer_token_key,
    }


async def _post_tool_call(
    body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    all_headers = {
        "Authorization": f"Bearer {TEST_BEARER_TOKEN}",
        **_SSO_HEADERS,
        **(headers or {}),
    }
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=all_headers
    ) as client:
        return await client.post("/tool-call", json=body)


def _assert_well_formed(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200
    body = response.json()
    assert set(body) in ({"data"}, {"error"})
    return body


@pytest.fixture(scope="module")
def echo_server() -> Iterator[dict[str, Any]]:
    """帶 echo_tool、list_tool(FastMCP 用 `{"result": [...]}` 包裝——證明端點從不拆封信封)、
    text_only_tool(無 structuredContent)、failing_tool(用 `fastmcp.exceptions.ToolError`,
    fastmcp 不會再包一層,message 逐字透傳)、slow_tool(拖久測 timeout)。"""
    mcp_server = FastMCP("fixture-tool-call-echo-server")

    @mcp_server.tool()
    def echo_tool(message: str) -> dict[str, Any]:
        return {"echo": message}

    @mcp_server.tool()
    def list_tool() -> list[dict[str, Any]]:
        return [{"order_id": "A-1", "qty": 3}]

    @mcp_server.tool(output_schema=None)
    def text_only_tool(message: str) -> str:
        return message

    @mcp_server.tool()
    def failing_tool() -> dict[str, Any]:
        raise ToolError(_FAILING_TOOL_MESSAGE)

    @mcp_server.tool()
    def slow_tool() -> dict[str, Any]:
        time.sleep(2)
        return {"ok": True}

    counting_app = RequestCountingMiddleware(mcp_server.http_app(stateless_http=True))
    port = free_port()
    server = run_server_in_thread(counting_app, port)

    yield {"base_url": f"http://127.0.0.1:{port}/mcp", "counts": counting_app.counts}

    server.should_exit = True


def _start_status_server(status: int) -> dict[str, Any]:
    mcp_server = FastMCP(f"fixture-status-{status}-server")

    @mcp_server.tool()
    def echo_tool(message: str) -> dict[str, Any]:
        return {"echo": message}

    counting_app = RequestCountingMiddleware(
        ForcedStatusMiddleware(mcp_server.http_app(stateless_http=True), forced_status=status)
    )
    port = free_port()
    server = run_server_in_thread(counting_app, port)
    return {
        "base_url": f"http://127.0.0.1:{port}/mcp",
        "counts": counting_app.counts,
        "server": server,
    }


@pytest.fixture(scope="module")
def status_server_401() -> Iterator[dict[str, Any]]:
    context = _start_status_server(401)
    yield context
    context["server"].should_exit = True


@pytest.fixture(scope="module")
def status_server_404() -> Iterator[dict[str, Any]]:
    context = _start_status_server(404)
    yield context
    context["server"].should_exit = True


@pytest.fixture(scope="module")
def status_server_503() -> Iterator[dict[str, Any]]:
    context = _start_status_server(503)
    yield context
    context["server"].should_exit = True


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("dropped_header", ["X-SSO-Token", "X-SSO-Url"])
async def test_tool_call_missing_sso_header_returns_auth(dropped_header: str, echo_server) -> None:
    echo_server["counts"].clear()
    headers = {"Authorization": f"Bearer {TEST_BEARER_TOKEN}", **_SSO_HEADERS}
    del headers[dropped_header]
    body_payload = {
        "connector": _connector(echo_server["base_url"]),
        "tool": "echo_tool",
        "args": {"message": "hi"},
    }

    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        response = await client.post("/tool-call", json=body_payload)

    settings = get_settings()
    header_name = (
        settings.SSO_TOKEN_HEADER if dropped_header == "X-SSO-Token" else settings.SSO_URL_HEADER
    )
    body = _assert_well_formed(response)
    assert body["error"]["code"] == "AUTH"
    assert body["error"]["message"] == f"sign-in required: missing {header_name}"
    assert echo_server["counts"] == {}, "no MCP request should have been sent before auth"


async def test_tool_call_unconfigured_bearer_key_returns_connector_unavailable(echo_server) -> None:
    response = await _post_tool_call(
        {
            "connector": _connector(echo_server["base_url"], bearer_token_key="missing-key"),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )

    body = _assert_well_formed(response)
    assert body["error"]["code"] == "CONNECTOR_UNAVAILABLE"
    assert "fixture" in body["error"]["message"]
    assert "missing-key" in body["error"]["message"]


async def test_tool_call_empty_tool_name_returns_invalid_call(echo_server) -> None:
    response = await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "", "args": {}}
    )

    body = _assert_well_formed(response)
    assert body == {"error": {"code": "INVALID_CALL", "message": "tool name is empty"}}


async def test_tool_call_non_object_args_returns_invalid_call(echo_server) -> None:
    response = await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "echo_tool", "args": [1, 2]}
    )

    body = _assert_well_formed(response)
    assert body == {
        "error": {"code": "INVALID_CALL", "message": "args must be a JSON object, got list"}
    }


async def test_tool_call_timeout_returns_retryable_after_configured_retries(
    echo_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONNECTOR_REQUEST_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "1")
    get_settings.cache_clear()

    response = await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "slow_tool", "args": {}}
    )

    body = _assert_well_formed(response)
    assert body["error"]["code"] == "RETRYABLE"
    assert re.fullmatch(
        r"connector 'fixture' did not respond \(\w+\) after 2 attempts; retry",
        body["error"]["message"],
    )


async def test_tool_call_connection_refused_returns_retryable() -> None:
    response = await _post_tool_call(
        {"connector": _connector(_UNREACHABLE_URL), "tool": "echo_tool", "args": {}}
    )

    body = _assert_well_formed(response)
    assert body["error"]["code"] == "RETRYABLE"
    assert "did not respond (" in body["error"]["message"]
    assert "; retry" in body["error"]["message"]


async def test_tool_call_http_401_returns_auth_without_retry(
    status_server_401, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_server_401["counts"].clear()
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "1")
    get_settings.cache_clear()

    response = await _post_tool_call(
        {
            "connector": _connector(status_server_401["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )
    body = _assert_well_formed(response)
    assert body == {
        "error": {
            "code": "AUTH",
            "message": "connector 'fixture' rejected your credentials (HTTP 401); sign in again",
        }
    }
    # 401 在 session initialize 那一步就被擋下(還沒走到 tools/call), 所以用當次
    # 送出的請求總數(counts 的值總和)量測有沒有真的重試, 而不是專看 tools/call。
    count_with_retries = sum(status_server_401["counts"].values())

    status_server_401["counts"].clear()
    monkeypatch.setenv("CONNECTOR_CALL_RETRIES", "0")
    get_settings.cache_clear()
    await _post_tool_call(
        {
            "connector": _connector(status_server_401["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )
    count_without_retries = sum(status_server_401["counts"].values())

    assert count_with_retries == count_without_retries == 1


async def test_tool_call_http_404_is_swallowed_as_session_terminated_returns_retryable(
    status_server_404,
) -> None:
    """DEVIATION from the plan's literal row-7 expectation (CONNECTOR_UNAVAILABLE): a forced 404
    on the very first request (session initialize) never reaches httpx as an HTTPStatusError --
    the mcp streamable-http client treats 404 specially as "session terminated" and raises
    McpError before any status code is attached to an exception, so ConnectorToolError ends up
    kind="transport", status=None, cause_name="McpError" (confirmed with a direct call_tool()
    probe outside the endpoint; see the task report for the traceback). Per plan Task 2 Step 5 /
    spec §10 item 1, when the status is not reachable the fallback is RETRYABLE."""
    response = await _post_tool_call(
        {
            "connector": _connector(status_server_404["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )

    body = _assert_well_formed(response)
    assert body["error"]["code"] == "RETRYABLE"
    assert "did not respond (" in body["error"]["message"]


async def test_tool_call_http_503_returns_retryable(status_server_503) -> None:
    response = await _post_tool_call(
        {
            "connector": _connector(status_server_503["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )

    body = _assert_well_formed(response)
    assert body == {
        "error": {"code": "RETRYABLE", "message": "connector 'fixture' returned HTTP 503; retry"}
    }


async def test_tool_call_is_error_returns_tool_error_with_server_text_verbatim(echo_server) -> None:
    response = await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "failing_tool", "args": {}}
    )

    body = _assert_well_formed(response)
    assert body == {"error": {"code": "TOOL_ERROR", "message": _FAILING_TOOL_MESSAGE}}


async def test_tool_call_no_structured_content_returns_connector_unavailable(echo_server) -> None:
    response = await _post_tool_call(
        {
            "connector": _connector(echo_server["base_url"]),
            "tool": "text_only_tool",
            "args": {"message": "hi"},
        }
    )

    body = _assert_well_formed(response)
    assert body == {
        "error": {
            "code": "CONNECTOR_UNAVAILABLE",
            "message": (
                "tool 'text_only_tool' on connector 'fixture' no longer returns structured "
                "data; ask the connector owner"
            ),
        }
    }


async def test_tool_call_unexpected_exception_returns_retryable_and_logs_traceback(
    echo_server, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _raise_key_error(*args: Any, **kwargs: Any) -> None:
        raise KeyError("boom")

    monkeypatch.setattr(tool_call_flow, "call_tool", _raise_key_error)

    with caplog.at_level("ERROR"):
        response = await _post_tool_call(
            {
                "connector": _connector(echo_server["base_url"]),
                "tool": "echo_tool",
                "args": {"message": "hi"},
            }
        )

    body = _assert_well_formed(response)
    assert body == {
        "error": {
            "code": "RETRYABLE",
            "message": "unexpected failure calling 'fixture.echo_tool' (KeyError); retry",
        }
    }
    error_records = [record for record in caplog.records if record.exc_info is not None]
    assert error_records, "row 11 fallback should log the traceback at ERROR"


async def test_tool_call_success_returns_structured_content_unchanged(echo_server) -> None:
    echo_response = await _post_tool_call(
        {
            "connector": _connector(echo_server["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )
    assert _assert_well_formed(echo_response) == {"data": {"echo": "hi"}}

    list_response = await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "list_tool", "args": {}}
    )
    assert _assert_well_formed(list_response) == {
        "data": {"result": [{"order_id": "A-1", "qty": 3}]}
    }


async def test_tool_call_never_unwraps_or_lands(
    echo_server, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    get_settings.cache_clear()

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("tool-call endpoint must never unwrap or land a response")

    monkeypatch.setattr(api_snapshot, "unwrap_envelope", _explode)
    monkeypatch.setattr(api_snapshot, "land_response", _explode)

    response = await _post_tool_call(
        {
            "connector": _connector(echo_server["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )

    assert _assert_well_formed(response) == {"data": {"echo": "hi"}}
    assert not (tmp_path / "ws").exists()
    assert not any(tmp_path.rglob("connector_calls.jsonl"))


async def test_tool_call_response_never_contains_secrets(
    echo_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CONNECTOR_BEARER_TOKENS", '{"secret-key": "bearer-secret-xyz"}')
    get_settings.cache_clear()

    class _LeakingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError(f"leak {_SSO_HEADERS['X-SSO-Token']} bearer-secret-xyz")

    monkeypatch.setattr(mcp_adapter, "Client", _LeakingClient)

    response = await _post_tool_call(
        {
            "connector": _connector(echo_server["base_url"], bearer_token_key="secret-key"),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )

    body = _assert_well_formed(response)
    assert "bearer-secret-xyz" not in body["error"]["message"]
    assert _SSO_HEADERS["X-SSO-Token"] not in body["error"]["message"]


async def test_tool_call_log_line_has_arg_keys_not_values(
    echo_server, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO"):
        await _post_tool_call(
            {
                "connector": _connector(echo_server["base_url"]),
                "tool": "echo_tool",
                "args": {"message": "value-must-not-be-logged"},
            }
        )

    matching_records = [
        record
        for record in caplog.records
        if re.fullmatch(
            r"tool_call connector=fixture tool=echo_tool arg_keys=\[message\] ms=\d+ ok=true code=-",
            record.message,
        )
    ]
    assert matching_records
    assert "value-must-not-be-logged" not in caplog.text


async def test_tool_call_bearer_missing_returns_401(echo_server) -> None:
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=_SSO_HEADERS
    ) as client:
        response = await client.post(
            "/tool-call",
            json={
                "connector": _connector(echo_server["base_url"]),
                "tool": "echo_tool",
                "args": {},
            },
        )

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


async def test_tool_call_malformed_body_returns_422() -> None:
    response = await _post_tool_call({"tool": "echo_tool", "args": {}})

    assert response.status_code == 422


async def test_tool_call_unknown_tool_returns_tool_error_with_server_text(echo_server) -> None:
    response = await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "no_such_tool", "args": {}}
    )

    body = _assert_well_formed(response)
    assert body["error"]["code"] == "TOOL_ERROR"
    assert "no_such_tool" in body["error"]["message"]
    assert body["error"]["message"].startswith("Unknown tool")


async def test_tool_call_never_calls_tools_list(echo_server) -> None:
    echo_server["counts"].clear()

    await _post_tool_call(
        {
            "connector": _connector(echo_server["base_url"]),
            "tool": "echo_tool",
            "args": {"message": "hi"},
        }
    )
    await _post_tool_call(
        {"connector": _connector(echo_server["base_url"]), "tool": "no_such_tool", "args": {}}
    )

    assert echo_server["counts"].get("tools/list", 0) == 0
    assert echo_server["counts"]["tools/call"] == 2
