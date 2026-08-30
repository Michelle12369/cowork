"""MCP stateless adapter 測試——用真的本地 FastMCP(`stateless_http=True`)fixture server
驗證 `load_mcp_connector`：tools/list 列舉、tools/call round-trip、每次呼叫的
Authorization header 真的送達伺服端(自寫的 ASGI middleware 攔截，見
`_HeaderCapturingMiddleware`)、MCP 端錯誤透傳成 `ConnectorToolError`、skill resource
讀取（含缺席時的空劇本＋warning）、缺身分時 `require_sso_token` fail loud（不送出未認證
請求）、伺服端不可達時包成可行動的 `ConnectorToolError`。

選 FastMCP 真實伺服器而非陽春 ASGI stub——重點是驗證 adapter 與真實 MCP SDK 的 stateless
streamable HTTP 線路相容，陽春 stub 測不出這件事。跑在背景執行緒的隨機埠上，模組層
fixture 全測試共用一個伺服器。
"""

import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from app.agent.connectors.mcp_adapter import load_mcp_connector
from app.agent.connectors.model import Connector, ConnectorToolError
from app.engine.request_context import reset_request_identity, set_request_identity

_FAILING_TOOL_MESSAGE = "上游資料源逾時，請縮小查詢範圍後重試"


class _CapturedRequest:
    __slots__ = ("authorization", "method_name")

    def __init__(self, method_name: str | None, authorization: str | None) -> None:
        self.method_name = method_name
        self.authorization = authorization


class _HeaderCapturingMiddleware:
    """包在 FastMCP streamable-http ASGI app 外層——只為了讓測試斷言 Authorization
    header 真的送達伺服端(見 mcp_adapter.py 模組 docstring 的 spike 結論)，不介入 MCP
    協定本身；body 讀出後原樣重放給下游 app，不改變回應內容。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self.captured: list[_CapturedRequest] = []

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        authorization_raw = headers.get(b"authorization")
        authorization = authorization_raw.decode() if authorization_raw is not None else None

        body_chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body = b"".join(body_chunks)

        method_name = None
        try:
            payload = json.loads(body or b"{}")
            method_name = payload.get("method")
        except json.JSONDecodeError:
            method_name = None

        self.captured.append(_CapturedRequest(method_name, authorization))

        replayed = False

        async def _replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            # 已重放完 body——後續 receive() 呼叫(例如串流回應期間的 client 斷線偵測)轉發
            # 給真正的底層 channel,不可合成 http.disconnect(會被誤判成 client 真的斷線,
            # 讓伺服端提早砍斷還在寫的 SSE 回應)。
            return await receive()

        await self._app(scope, _replay_receive, send)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return probe_socket.getsockname()[1]


def _run_server_in_thread(app: Any, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return server
        time.sleep(0.02)
    raise RuntimeError("fixture uvicorn server 未在時限內就緒")


@pytest.fixture(scope="module")
def echo_server() -> Iterator[dict[str, Any]]:
    """帶一個 echo tool、一個 failing tool、一個 skill resource 的 fixture server。"""
    mcp_server = FastMCP("fixture-echo-server", stateless_http=True)

    @mcp_server.tool()
    def echo_tool(message: str) -> dict[str, Any]:
        """回傳原樣訊息，驗證 tools/call round-trip。"""
        return {"echo": message}

    @mcp_server.tool()
    def failing_tool() -> dict[str, Any]:
        """恆拋錯——驗證 MCP isError 回應透傳成 ConnectorToolError。"""
        raise ValueError(_FAILING_TOOL_MESSAGE)

    @mcp_server.resource("skill://usage")
    def usage_resource() -> str:
        return "# fixture 劇本\n\nload_mcp_connector 讀 skill://usage 驗證用。"

    capturing_app = _HeaderCapturingMiddleware(mcp_server.streamable_http_app())
    port = _free_port()
    server = _run_server_in_thread(capturing_app, port)

    yield {"base_url": f"http://127.0.0.1:{port}/mcp", "captured": capturing_app.captured}

    server.should_exit = True


@pytest.fixture(scope="module")
def no_skill_server() -> Iterator[str]:
    """無任何 resource 的 fixture server——驗證 skill 缺席時空劇本＋warning 的分支。"""
    mcp_server = FastMCP("fixture-no-skill-server", stateless_http=True)

    @mcp_server.tool()
    def noop_tool() -> dict[str, Any]:
        return {"ok": True}

    port = _free_port()
    server = _run_server_in_thread(mcp_server.streamable_http_app(), port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


@contextlib.contextmanager
def _identity(sso_token: str | None = "test-bearer-token") -> Iterator[None]:
    tokens = set_request_identity("user-1", "session-1", sso_token)
    try:
        yield
    finally:
        reset_request_identity(tokens)


def _tool_by_name(connector: Connector, tool_name: str):
    return next(tool for tool in connector.tools if tool.name == tool_name)


def test_load_mcp_connector_enumerates_tools_with_input_schema(echo_server) -> None:
    with _identity():
        connector = load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert connector.connector_id == "fixture"
    assert connector.display_name == "Fixture Server"
    tool_names = {tool.name for tool in connector.tools}
    assert tool_names == {"echo_tool", "failing_tool"}

    echo_tool = _tool_by_name(connector, "echo_tool")
    assert echo_tool.input_schema["type"] == "object"
    assert "message" in echo_tool.input_schema["properties"]
    assert echo_tool.input_schema["required"] == ["message"]


def test_tool_call_round_trips_args_and_returns_parsed_json(echo_server) -> None:
    with _identity():
        connector = load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        result = echo_tool.call({"message": "hello mcp"})

    assert result == {"echo": "hello mcp"}


def test_tool_call_sends_authorization_header_with_current_token(echo_server) -> None:
    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="call-time-token-42"):
        connector = load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        echo_tool.call({"message": "check header"})

    tool_call_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert tool_call_requests, "tools/call 請求未送達伺服端"
    assert tool_call_requests[-1].authorization == "Bearer call-time-token-42"

    # tools/list(load 階段)也要帶上呼叫當下的 token——同屬「每次呼叫」。
    list_requests = [entry for entry in captured if entry.method_name == "tools/list"]
    assert list_requests
    assert list_requests[-1].authorization == "Bearer call-time-token-42"

    # resources/read(劇本讀取，同屬 load 階段)也要帶上同一個 token。
    resource_read_requests = [entry for entry in captured if entry.method_name == "resources/read"]
    assert resource_read_requests
    assert resource_read_requests[-1].authorization == "Bearer call-time-token-42"


def test_missing_identity_raises_lookup_error_without_calling_server(echo_server) -> None:
    captured = echo_server["captured"]
    captured.clear()

    with pytest.raises(LookupError):
        load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert captured == [], "缺身分時不應該送出任何未認證請求"


def test_server_error_raises_connector_tool_error_with_verbatim_message(echo_server) -> None:
    with _identity():
        connector = load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])
        failing_tool = _tool_by_name(connector, "failing_tool")

        with pytest.raises(ConnectorToolError, match=_FAILING_TOOL_MESSAGE):
            failing_tool.call({})


def test_resource_read_populates_skill_markdown(echo_server) -> None:
    with _identity():
        connector = load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert "fixture 劇本" in connector.skill_markdown


def test_missing_skill_resource_returns_empty_skill_and_warns(no_skill_server, caplog) -> None:
    with caplog.at_level("WARNING"), _identity():
        connector = load_mcp_connector("no-skill", "No Skill Server", no_skill_server)

    assert connector.skill_markdown == ""
    assert any("skill" in record.message.lower() for record in caplog.records)


def test_unreachable_server_raises_connector_tool_error_without_leaking_token() -> None:
    with (
        _identity(sso_token="must-not-leak-token"),
        pytest.raises(ConnectorToolError) as error_info,
    ):
        load_mcp_connector("unreachable", "Unreachable Server", "http://127.0.0.1:1/mcp")

    assert "must-not-leak-token" not in str(error_info.value)


def test_unreachable_server_error_message_is_actionable() -> None:
    with (
        _identity(),
        pytest.raises(ConnectorToolError, match="tools/list|連線|連接|unreachable|MCP"),
    ):
        load_mcp_connector("unreachable", "Unreachable Server", "http://127.0.0.1:1/mcp")


def test_http_status_error_message_includes_status_code_for_diagnosis(echo_server) -> None:
    """訊息 MUST 帶狀態碼，401 才分辨得出跟 500/timeout 不同。用 fixture server 一個未
    掛載的路徑觸發 Starlette 404(FastMCP 只在 `streamable_http_path`＝`/mcp` 掛路由，
    打旁邊的路徑會被路由層擋下回 404，不需要另外起一個會回真的 401/500 的 stub server)。"""
    wrong_path_base_url = echo_server["base_url"] + "-not-a-real-path"

    with _identity(), pytest.raises(ConnectorToolError, match="404") as error_info:
        load_mcp_connector("fixture", "Fixture Server", wrong_path_base_url)

    assert "test-bearer-token" not in str(error_info.value)
