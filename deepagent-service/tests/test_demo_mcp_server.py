"""驗證 `demo_mcp_server/server.py`（dev/demo compose profile 用的合成品質資料 MCP
server）真的講對 `load_mcp_connector`（純 MCP adapter）期待的方言——起一個真正的
uvicorn 背景執行緒(pattern 同 tests/test_mcp_adapter.py)，對它跑 `load_mcp_connector`，
斷言 `list_fabs`/`get_quality` 兩個 tool 與 `skill://usage` resource round-trip成功，
且回傳內容與 `registry.demo_connector()`(pytest 直組版)一致——證明兩個入口（純測試
fixture／真正跑起來的 demo server）沒有分岔。
"""

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn

from app.agent.connectors.mcp_adapter import load_mcp_connector
from app.agent.connectors.registry import demo_connector
from app.engine.request_context import reset_request_identity, set_request_identity
from demo_mcp_server.server import app as demo_mcp_asgi_app


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
    raise RuntimeError("demo mcp server 未在時限內就緒")


@pytest.fixture(scope="module")
def demo_mcp_base_url() -> Iterator[str]:
    port = _free_port()
    server = _run_server_in_thread(demo_mcp_asgi_app, port)

    yield f"http://127.0.0.1:{port}/mcp"

    server.should_exit = True


def _tool_by_name(connector, tool_name: str):
    return next(tool for tool in connector.tools if tool.name == tool_name)


def test_demo_mcp_server_round_trips_list_fabs_get_quality_and_skill(demo_mcp_base_url) -> None:
    tokens = set_request_identity("user-1", "session-1", "test-token", None)
    try:
        connector = load_mcp_connector("demo_quality", "示範品質資料（合成）", demo_mcp_base_url)

        tool_names = {tool.name for tool in connector.tools}
        assert tool_names == {"list_fabs", "get_quality"}

        list_fabs_result = _tool_by_name(connector, "list_fabs").call({})
        get_quality_result = _tool_by_name(connector, "get_quality").call(
            {"fab": "FAB_A", "week": "2026-W32"}
        )
    finally:
        reset_request_identity(tokens)

    fixture_connector = demo_connector()
    fixture_list_fabs = next(
        tool for tool in fixture_connector.tools if tool.name == "list_fabs"
    ).call({})
    fixture_get_quality = next(
        tool for tool in fixture_connector.tools if tool.name == "get_quality"
    ).call({"fab": "FAB_A", "week": "2026-W32"})

    # 真正跑起來的 demo server 與 pytest 直組版(registry.demo_connector())資料一致——
    # 兩個入口沒有分岔。
    assert list_fabs_result == fixture_list_fabs
    assert get_quality_result == fixture_get_quality
    assert get_quality_result["errorCode"] == ""
    assert len(get_quality_result["data"]) == 9

    assert connector.skill_markdown.strip() == fixture_connector.skill_markdown.strip()
    assert "demo_quality 操作劇本" in connector.skill_markdown


def test_demo_mcp_server_unknown_fab_raises_actionable_connector_tool_error(
    demo_mcp_base_url,
) -> None:
    from app.agent.connectors.model import ConnectorToolError

    tokens = set_request_identity("user-1", "session-1", "test-token", None)
    try:
        connector = load_mcp_connector("demo_quality", "示範品質資料（合成）", demo_mcp_base_url)
        get_quality = _tool_by_name(connector, "get_quality")

        with pytest.raises(ConnectorToolError, match="未知的 fab"):
            get_quality.call({"fab": "FAB_NOPE", "week": "2026-W32"})
    finally:
        reset_request_identity(tokens)
