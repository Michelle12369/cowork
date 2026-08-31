"""MCP stateless adapter 測試——用真的本地 FastMCP(`stateless_http=True`)fixture server
驗證 `load_mcp_connector`：tools/list 列舉、tools/call round-trip、每次呼叫的可配置 SSO
header(`Settings.CONNECTOR_SSO_TOKEN_HEADER`/`CONNECTOR_SSO_URL_HEADER`)真的以設定的名稱
送達伺服端(自寫的 ASGI middleware 攔截，見 `_HeaderCapturingMiddleware`)、MCP 端錯誤透傳
成 `ConnectorToolError`、多 `skill://` resource 逐一讀取成 `Connector.skills`（含零 resource
時的空劇本＋warning、單一 resource 讀取失敗不拖累其他 resource）、缺身分時
`require_sso_token` fail loud（不送出未認證請求）、伺服端不可達時包成可行動的
`ConnectorToolError`。

選 FastMCP 真實伺服器而非陽春 ASGI stub——重點是驗證 adapter 與真實 MCP SDK 的 stateless
streamable HTTP 線路相容，陽春 stub 測不出這件事。跑在背景執行緒的隨機埠上，模組層
fixture 全測試共用一個伺服器。
"""

import asyncio
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
from app.config import get_settings
from app.engine.request_context import reset_request_identity, set_request_identity

_FAILING_TOOL_MESSAGE = "上游資料源逾時，請縮小查詢範圍後重試"


class _CapturedRequest:
    __slots__ = ("headers", "method_name")

    def __init__(self, method_name: str | None, headers: dict[str, str]) -> None:
        self.method_name = method_name
        self.headers = headers

    def header(self, name: str) -> str | None:
        """大小寫不敏感取值——HTTP header 名稱本就不分大小寫，測試斷言不該綁死大小寫。"""
        return self.headers.get(name.lower())


class _HeaderCapturingMiddleware:
    """包在 FastMCP streamable-http ASGI app 外層——只為了讓測試斷言可配置的 SSO token/url
    header 真的以設定的名稱送達伺服端(見 mcp_adapter.py 模組 docstring)，不介入 MCP 協定
    本身；body 讀出後原樣重放給下游 app，不改變回應內容。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self.captured: list[_CapturedRequest] = []

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        raw_headers = dict(scope.get("headers") or [])
        headers = {name.decode().lower(): value.decode() for name, value in raw_headers.items()}

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

        self.captured.append(_CapturedRequest(method_name, headers))

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


class _ForcedStatusMiddleware:
    """把底層 app 的每個 HTTP 回應狀態碼強制改寫成固定值——SDK client 對非 202/404 狀態碼
    走 `httpx.raise_for_status()`,訊息裡會帶原始狀態碼文字,用來驗證診斷用的狀態碼確實
    透傳到 `ConnectorToolError` 訊息(見 `test_http_status_error_message_includes_status_code_for_diagnosis`)。"""

    def __init__(self, app: Any, forced_status: int) -> None:
        self._app = app
        self._forced_status = forced_status

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["status"] = self._forced_status
            await send(message)

        await self._app(scope, receive, send_wrapper)


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
    """帶一個 echo tool、一個純 text-content echo tool、一個 failing tool、兩個 skill
    resource(`skill://usage`／`skill://advanced`，驗證一個 server 供多個劇本)的 fixture
    server。"""
    mcp_server = FastMCP("fixture-echo-server", stateless_http=True)

    @mcp_server.tool()
    def echo_tool(message: str) -> dict[str, Any]:
        """回傳原樣訊息，驗證 tools/call round-trip(structuredContent 路徑)。"""
        return {"echo": message}

    @mcp_server.tool(structured_output=False)
    def text_only_echo_tool(message: str) -> dict[str, Any]:
        """`structured_output=False`——server 只給 content text block、無
        structuredContent，驗證 adapter 退回解析 text 當 JSON 的路徑。"""
        return {"echo": message}

    @mcp_server.tool()
    def failing_tool() -> dict[str, Any]:
        """恆拋錯——驗證 MCP isError 回應透傳成 ConnectorToolError。"""
        raise ValueError(_FAILING_TOOL_MESSAGE)

    @mcp_server.resource("skill://usage")
    def usage_resource() -> str:
        return "# fixture 劇本(usage)\n\nload_mcp_connector 讀 skill://usage 驗證用。"

    @mcp_server.resource("skill://advanced")
    def advanced_resource() -> str:
        return "# fixture 劇本(advanced)\n\n驗證一個 connector 供多份劇本。"

    capturing_app = _HeaderCapturingMiddleware(mcp_server.streamable_http_app())
    port = _free_port()
    server = _run_server_in_thread(capturing_app, port)

    yield {"base_url": f"http://127.0.0.1:{port}/mcp", "captured": capturing_app.captured}

    server.should_exit = True


@pytest.fixture(scope="module")
def partial_skill_server() -> Iterator[str]:
    """一個 skill resource 正常、一個讀取時恆拋錯的 fixture server——驗證單一 skill 讀取
    失敗只跳過該份、不拖累其他 skill(partial success)。"""
    mcp_server = FastMCP("fixture-partial-skill-server", stateless_http=True)

    @mcp_server.tool()
    def noop_tool() -> dict[str, Any]:
        return {"ok": True}

    @mcp_server.resource("skill://usage")
    def usage_resource() -> str:
        return "# 可讀劇本"

    @mcp_server.resource("skill://broken")
    def broken_resource() -> str:
        raise ValueError("boom-resource")

    port = _free_port()
    server = _run_server_in_thread(mcp_server.streamable_http_app(), port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


@pytest.fixture(scope="module")
def unauthorized_server() -> Iterator[str]:
    """所有回應強制改寫成 401——模擬上游 gateway 拒絕請求；用來驗證 `ConnectorToolError`
    訊息帶狀態碼(見 `test_http_status_error_message_includes_status_code_for_diagnosis`)。"""
    mcp_server = FastMCP("fixture-unauthorized-server", stateless_http=True)

    @mcp_server.tool()
    def unreachable_tool() -> dict[str, Any]:
        return {"ok": True}

    app = _ForcedStatusMiddleware(mcp_server.streamable_http_app(), forced_status=401)
    port = _free_port()
    server = _run_server_in_thread(app, port)

    yield f"http://127.0.0.1:{port}/mcp"

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


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """本檔部分測試以 env var override `CONNECTOR_SSO_TOKEN_HEADER`/
    `CONNECTOR_SSO_URL_HEADER` 證明 header 名稱可配置——`get_settings()` 有
    `lru_cache`,前後都要清才不會漏到別的測試。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@contextlib.contextmanager
def _identity(
    sso_token: str | None = "test-bearer-token", sso_url: str | None = None
) -> Iterator[None]:
    tokens = set_request_identity("user-1", "session-1", sso_token, sso_url)
    try:
        yield
    finally:
        reset_request_identity(tokens)


def _tool_by_name(connector: Connector, tool_name: str):
    return next(tool for tool in connector.tools if tool.name == tool_name)


def _load(connector_id: str, display_name: str, base_url: str) -> Connector:
    """給 sync-by-design 的測試用：這些測試接著呼叫 `ConnectorTool.call`(內部自己
    `asyncio.run()`),測試函式本身 MUST 沒有 running loop,故不能是 `async def`——
    這裡改用 `asyncio.run()` 承接 async 的 `load_mcp_connector`。"""
    return asyncio.run(load_mcp_connector(connector_id, display_name, base_url))


async def test_load_mcp_connector_enumerates_tools_with_input_schema(echo_server) -> None:
    with _identity():
        connector = await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert connector.connector_id == "fixture"
    assert connector.display_name == "Fixture Server"
    tool_names = {tool.name for tool in connector.tools}
    assert tool_names == {"echo_tool", "text_only_echo_tool", "failing_tool"}

    echo_tool = _tool_by_name(connector, "echo_tool")
    assert echo_tool.input_schema["type"] == "object"
    assert "message" in echo_tool.input_schema["properties"]
    assert echo_tool.input_schema["required"] == ["message"]


def test_tool_call_round_trips_args_and_returns_parsed_json(echo_server) -> None:
    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        result = echo_tool.call({"message": "hello mcp"})

    assert result == {"echo": "hello mcp"}


def test_tool_call_without_structured_content_raises_actionable_error(
    echo_server,
) -> None:
    """structuredContent-only 契約：`structured_output=False` 的 tool 只給 text block——
    adapter 不再退回解析 text，直接以可行動訊息拒收。"""
    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        text_only_tool = _tool_by_name(connector, "text_only_echo_tool")
        with pytest.raises(ConnectorToolError, match="structuredContent"):
            text_only_tool.call({"message": "hello text-only"})


def test_tool_call_sends_default_sso_token_header_with_current_token(echo_server) -> None:
    """預設 header 名稱 X-SSO-Token(`Settings.CONNECTOR_SSO_TOKEN_HEADER` 未覆寫時)。"""
    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="call-time-token-42"):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        echo_tool = _tool_by_name(connector, "echo_tool")
        echo_tool.call({"message": "check header"})

    tool_call_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert tool_call_requests, "tools/call 請求未送達伺服端"
    assert tool_call_requests[-1].header("X-SSO-Token") == "call-time-token-42"

    # tools/list(load 階段)也要帶上呼叫當下的 token——同屬「每次呼叫」。
    list_requests = [entry for entry in captured if entry.method_name == "tools/list"]
    assert list_requests
    assert list_requests[-1].header("X-SSO-Token") == "call-time-token-42"

    # resources/read(劇本讀取，同屬 load 階段)也要帶上同一個 token。
    resource_read_requests = [entry for entry in captured if entry.method_name == "resources/read"]
    assert resource_read_requests
    assert resource_read_requests[-1].header("X-SSO-Token") == "call-time-token-42"


def test_tool_call_sends_sso_url_header_only_when_set(echo_server) -> None:
    """url header 只在 `current_sso_url` 有值時才附加——dev/無 SSO 環境沒有 ssoUrl 是常態,
    不該逼出一個空字串 header。"""
    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="tok", sso_url="https://sso.internal.example/auth"):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        _tool_by_name(connector, "echo_tool").call({"message": "with url"})

    with_url_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert with_url_requests
    assert with_url_requests[-1].header("X-SSO-Url") == "https://sso.internal.example/auth"

    captured.clear()
    with _identity(sso_token="tok", sso_url=None):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        _tool_by_name(connector, "echo_tool").call({"message": "without url"})

    without_url_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert without_url_requests
    assert without_url_requests[-1].header("X-SSO-Url") is None


def test_tool_call_uses_configured_header_names(echo_server, monkeypatch) -> None:
    """`CONNECTOR_SSO_TOKEN_HEADER`/`CONNECTOR_SSO_URL_HEADER` 覆寫後,adapter 改用新名稱
    送出 token/url——internal 環境的 connector API 可能要求與預設不同的 header 名稱。"""
    monkeypatch.setenv("CONNECTOR_SSO_TOKEN_HEADER", "X-Internal-Token")
    monkeypatch.setenv("CONNECTOR_SSO_URL_HEADER", "X-Internal-Url")
    get_settings.cache_clear()

    captured = echo_server["captured"]
    captured.clear()

    with _identity(sso_token="custom-header-token", sso_url="https://sso.internal.example/auth"):
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        _tool_by_name(connector, "echo_tool").call({"message": "custom headers"})

    tool_call_requests = [entry for entry in captured if entry.method_name == "tools/call"]
    assert tool_call_requests
    request = tool_call_requests[-1]
    assert request.header("X-Internal-Token") == "custom-header-token"
    assert request.header("X-Internal-Url") == "https://sso.internal.example/auth"
    # 舊的預設名稱不該同時出現——確認是「改名」而非「兩者都送」。
    assert request.header("X-SSO-Token") is None
    assert request.header("X-SSO-Url") is None


async def test_missing_identity_raises_lookup_error_without_calling_server(echo_server) -> None:
    captured = echo_server["captured"]
    captured.clear()

    with pytest.raises(LookupError):
        await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert captured == [], "缺身分時不應該送出任何未認證請求"


def test_server_error_raises_connector_tool_error_with_verbatim_message(echo_server) -> None:
    with _identity():
        connector = _load("fixture", "Fixture Server", echo_server["base_url"])
        failing_tool = _tool_by_name(connector, "failing_tool")

        with pytest.raises(ConnectorToolError, match=_FAILING_TOOL_MESSAGE):
            failing_tool.call({})


async def test_resources_list_loads_every_skill_scheme_resource_by_normalized_name(
    echo_server,
) -> None:
    """每個 `skill://` resource 都成為一個獨立 skill，name 為 URI 正規化結果——
    `skill://usage` 慣例上仍是主劇本，但這裡不特殊處理，與 `skill://advanced` 一視同仁。"""
    with _identity():
        connector = await load_mcp_connector("fixture", "Fixture Server", echo_server["base_url"])

    assert set(connector.skills) == {"usage", "advanced"}
    assert "fixture 劇本(usage)" in connector.skills["usage"]
    assert "fixture 劇本(advanced)" in connector.skills["advanced"]


async def test_missing_skill_resource_returns_empty_skill_and_warns(
    no_skill_server, caplog
) -> None:
    with caplog.at_level("WARNING"), _identity():
        connector = await load_mcp_connector("no-skill", "No Skill Server", no_skill_server)

    assert connector.skills == {}
    assert any("skill" in record.message.lower() for record in caplog.records)


async def test_one_skill_resource_read_failure_does_not_block_other_skills(
    partial_skill_server, caplog
) -> None:
    with caplog.at_level("WARNING"), _identity():
        connector = await load_mcp_connector(
            "partial-skill", "Partial Skill Server", partial_skill_server
        )

    assert connector.skills == {"usage": "# 可讀劇本"}
    assert any(
        "broken" in record.message and "partial-skill" in record.message
        for record in caplog.records
    )


async def test_unreachable_server_raises_connector_tool_error_without_leaking_token() -> None:
    with (
        _identity(sso_token="must-not-leak-token"),
        pytest.raises(ConnectorToolError) as error_info,
    ):
        await load_mcp_connector("unreachable", "Unreachable Server", "http://127.0.0.1:1/mcp")

    assert "must-not-leak-token" not in str(error_info.value)


async def test_unreachable_server_error_message_is_actionable() -> None:
    with (
        _identity(),
        pytest.raises(ConnectorToolError, match="tools/list|連線|連接|unreachable|MCP"),
    ):
        await load_mcp_connector("unreachable", "Unreachable Server", "http://127.0.0.1:1/mcp")


async def test_http_status_error_message_includes_status_code_for_diagnosis(
    unauthorized_server,
) -> None:
    """訊息 MUST 帶狀態碼，401 才分辨得出跟 500/timeout 不同。用 `_ForcedStatusMiddleware`
    把每個回應狀態碼強制改成 401(模擬上游 gateway 拒絕請求)——SDK client 對非 202/404 狀態碼
    走 `httpx.raise_for_status()`，訊息裡帶原始狀態碼文字。注意：MCP streamable-http 協定對
    404 有特殊語意(session 失效，見 `mcp.client.streamable_http`)，SDK 會把它改寫成不帶
    狀態碼的『Session terminated』，故用 401 而非 404 驗證這條「狀態碼透傳」的規則。"""
    with (
        _identity(sso_token="must-not-leak-401-token"),
        pytest.raises(ConnectorToolError, match="401") as error_info,
    ):
        await load_mcp_connector("fixture", "Fixture Server", unauthorized_server)

    assert "must-not-leak-401-token" not in str(error_info.value)
